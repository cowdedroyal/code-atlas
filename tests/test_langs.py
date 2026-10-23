"""Cross-file resolution for the spec-walker languages, one realistic mini-repository each."""

import sqlite3

import pytest

from code_atlas.scan import scan
from lang_fixtures import C, CPP, CSHARP, GO, JAVA, RUST
from test_scan import write


def scanned(tmp_path, files):
    write(tmp_path, files)
    scan(tmp_path)
    db = sqlite3.connect(tmp_path / ".codemap" / "cache" / "index.sqlite")
    edges = {(s, d, t) for s, d, t in db.execute("select src, dst, type from edges")}
    syms = {i: (k, p) for i, k, p in db.execute("select id, kind, parent from symbols")}
    db.close()
    return edges, syms


CASES = {
    "go": (GO, [
        ("cmd/server/main.go#main", "internal/orders/service.go#NewService", "calls"),          # pkg alias → dir
        ("cmd/server/main.go#main", "internal/orders/service.go#Service.Place", "calls"),      # NewService() → *Service
        ("cmd/server/main.go#main", "internal/payments/charge.go#Charge", "calls"),            # renamed import
        ("internal/orders/service.go#Service.Place", "internal/orders/store.go#Store.Save", "calls"),  # field type chain
        ("internal/orders/service.go#Service.Place", "internal/orders/store.go#price", "calls"),       # same package
    ], {"internal/orders/store.go#Store.Save": ("method", "internal/orders/store.go#Store")}),
    "java": (JAVA, [
        ("src/main/java/com/shop/App.java#App.main", "src/main/java/com/shop/orders/OrderService.java#OrderService", "instantiates"),
        ("src/main/java/com/shop/App.java#App.main", "src/main/java/com/shop/orders/OrderService.java#OrderService.place", "calls"),
        ("src/main/java/com/shop/App.java#App.main", "src/main/java/com/shop/payments/Gateway.java#Gateway.charge", "calls"),  # a.b.*
        ("src/main/java/com/shop/orders/OrderService.java#OrderService.place", "src/main/java/com/shop/orders/OrderStore.java#OrderStore.save", "calls"),
        ("src/main/java/com/shop/payments/Gateway.java#Gateway.charge", "src/main/java/com/shop/payments/BaseGateway.java#BaseGateway.send", "calls"),
        ("src/main/java/com/shop/payments/Gateway.java#Gateway", "src/main/java/com/shop/payments/BaseGateway.java#BaseGateway", "inherits"),
    ], {"src/main/java/com/shop/orders/Order.java#Order": ("class", "src/main/java/com/shop/orders/Order.java")}),
    "rust": (RUST, [
        ("src/main.rs#main", "src/orders/service.rs#OrderService.new", "calls"),      # use crate::…::OrderService
        ("src/main.rs#main", "src/orders/service.rs#OrderService.place", "calls"),    # let svc = T::new()
        ("src/main.rs#main", "src/payments.rs#charge", "calls"),                      # call inside println!
        ("src/orders/service.rs#OrderService.place", "src/orders/store.rs#Store.save", "calls"),  # self.store field
        ("src/orders/service.rs#OrderService.place", "src/orders/store.rs#Order.new", "calls"),   # use super::store
        ("src/main.rs", "src/orders/mod.rs", "imports"),                              # mod orders;
    ], {"src/orders/service.rs#OrderService.place": ("method", "src/orders/service.rs#OrderService")}),
    "c": (C, [
        ("src/main.c#main", "src/orders/orders.c#place_order", "calls"),    # declaration in the header → definition
        ("src/main.c#main", "src/payments.c#charge", "calls"),
        ("src/orders/orders.c#place_order", "src/pricing.c#price_of", "calls"),  # ../pricing.h
        ("src/main.c", "src/orders/orders.h", "imports"),
    ], {"src/payments.h#charge": ("function", "src/payments.h")}),
    "cpp": (CPP, [
        ("src/main.cpp#main", "src/order_service.cpp#OrderService.place", "calls"),      # shop::OrderService svc
        ("src/order_service.cpp#OrderService.place", "src/store.cpp#Store.save", "calls"),  # store_ field from the header
        ("src/order_service.cpp#OrderService.place", "src/store.cpp#price", "calls"),
        ("src/main.cpp", "include/shop/order_service.hpp", "imports"),
    ], {"src/order_service.cpp#OrderService.place": ("method", "src/order_service.cpp")}),
    "csharp": (CSHARP, [
        ("Shop/Program.cs#Program.Main", "Shop/Orders/OrderService.cs#OrderService", "instantiates"),  # using Shop.Orders
        ("Shop/Program.cs#Program.Main", "Shop/Orders/OrderService.cs#OrderService.Place", "calls"),
        ("Shop/Program.cs#Program.Main", "Shop/Payments/Gateway.cs#Gateway.Charge", "calls"),
        ("Shop/Orders/OrderService.cs#OrderService.Place", "Shop/Orders/OrderStore.cs#OrderStore.Save", "calls"),  # same namespace
        ("Shop/Payments/Gateway.cs#Gateway.Charge", "Shop/Payments/Gateway.cs#Gateway.Send", "calls"),
    ], {"Shop/Orders/Order.cs#Order": ("class", "Shop/Orders/Order.cs")}),
}


@pytest.mark.parametrize("lang", sorted(CASES))
def test_cross_file_resolution(tmp_path, lang):
    files, expected_edges, expected_syms = CASES[lang]
    edges, syms = scanned(tmp_path, files)
    missing = [e for e in expected_edges if e not in edges]
    assert not missing, f"{lang}: missing {missing}"
    for sid, (kind, parent) in expected_syms.items():
        assert syms.get(sid) == (kind, parent), f"{lang}: {sid} → {syms.get(sid)}"
    # no noise symbols: package / module declarations are not symbols
    assert not any(k == "namespace" for k, _ in syms.values())
