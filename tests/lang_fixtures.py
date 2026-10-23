"""Small but realistic repositories, one per tier-2 language, used to check that cross-file links resolve.

Each fixture has the shape of a real project in that language: a module/package layout, imports across
directories, a type with methods, and calls through imported names.
"""

GO = {
    "go.mod": "module example.com/shop\n\ngo 1.22\n",
    "cmd/server/main.go": '''
        package main

        import (
            "fmt"

            "example.com/shop/internal/orders"
            pay "example.com/shop/internal/payments"
        )

        func main() {
            svc := orders.NewService()
            o := svc.Place("book")
            fmt.Println(pay.Charge(o.Total))
        }
    ''',
    "internal/orders/service.go": '''
        package orders

        type Service struct{ store *Store }

        func NewService() *Service { return &Service{store: newStore()} }

        func (s *Service) Place(item string) Order {
            o := Order{Item: item, Total: price(item)}
            s.store.Save(o)
            return o
        }
    ''',
    "internal/orders/store.go": '''
        package orders

        type Order struct {
            Item  string
            Total int
        }

        type Store struct{ items []Order }

        func newStore() *Store { return &Store{} }

        func (st *Store) Save(o Order) { st.items = append(st.items, o) }

        func price(item string) int { return len(item) * 100 }
    ''',
    "internal/payments/charge.go": '''
        package payments

        func Charge(amount int) string { return gateway(amount) }

        func gateway(amount int) string { return "ok" }
    ''',
}

JAVA = {
    "src/main/java/com/shop/App.java": '''
        package com.shop;

        import com.shop.orders.OrderService;
        import com.shop.orders.Order;
        import com.shop.payments.*;

        public class App {
            public static void main(String[] args) {
                OrderService svc = new OrderService();
                Order o = svc.place("book");
                Gateway.charge(o.total());
            }
        }
    ''',
    "src/main/java/com/shop/orders/OrderService.java": '''
        package com.shop.orders;

        public class OrderService {
            private final OrderStore store = new OrderStore();

            public Order place(String item) {
                Order o = new Order(item, Pricing.price(item));
                store.save(o);
                return o;
            }
        }
    ''',
    "src/main/java/com/shop/orders/Order.java": '''
        package com.shop.orders;

        public record Order(String item, int total) {}
    ''',
    "src/main/java/com/shop/orders/OrderStore.java": '''
        package com.shop.orders;

        import java.util.ArrayList;
        import java.util.List;

        class OrderStore {
            private final List<Order> items = new ArrayList<>();
            void save(Order o) { items.add(o); }
        }
    ''',
    "src/main/java/com/shop/orders/Pricing.java": '''
        package com.shop.orders;

        final class Pricing {
            static int price(String item) { return item.length() * 100; }
        }
    ''',
    "src/main/java/com/shop/payments/Gateway.java": '''
        package com.shop.payments;

        public class Gateway extends BaseGateway {
            public static String charge(int amount) { return send(amount); }
        }
    ''',
    "src/main/java/com/shop/payments/BaseGateway.java": '''
        package com.shop.payments;

        abstract class BaseGateway {
            protected static String send(int amount) { return "ok"; }
        }
    ''',
}

RUST = {
    "Cargo.toml": '[package]\nname = "shop"\nversion = "0.1.0"\n',
    "src/main.rs": '''
        mod orders;
        mod payments;

        use crate::orders::service::OrderService;
        use payments::charge;

        fn main() {
            let svc = OrderService::new();
            let o = svc.place("book");
            println!("{}", charge(o.total));
        }
    ''',
    "src/orders/mod.rs": '''
        pub mod service;
        pub mod store;
    ''',
    "src/orders/service.rs": '''
        use super::store::{Order, Store};

        pub struct OrderService { store: Store }

        impl OrderService {
            pub fn new() -> Self { OrderService { store: Store::default() } }

            pub fn place(&self, item: &str) -> Order {
                let o = Order::new(item, price(item));
                self.store.save(&o);
                o
            }
        }

        fn price(item: &str) -> u32 { item.len() as u32 * 100 }
    ''',
    "src/orders/store.rs": '''
        #[derive(Default)]
        pub struct Store;

        pub struct Order { pub item: String, pub total: u32 }

        impl Order {
            pub fn new(item: &str, total: u32) -> Self { Order { item: item.to_string(), total } }
        }

        impl Store {
            pub fn save(&self, _o: &Order) {}
        }
    ''',
    "src/payments.rs": '''
        pub fn charge(amount: u32) -> String { gateway(amount) }

        fn gateway(_amount: u32) -> String { "ok".into() }
    ''',
}

C = {
    "src/main.c": '''
        #include <stdio.h>
        #include "orders/orders.h"
        #include "payments.h"

        int main(void) {
            struct order o = place_order("book");
            printf("%s\\n", charge(o.total));
            return 0;
        }
    ''',
    "src/orders/orders.h": '''
        #ifndef ORDERS_H
        #define ORDERS_H
        struct order { const char *item; int total; };
        struct order place_order(const char *item);
        #endif
    ''',
    "src/orders/orders.c": '''
        #include "orders.h"
        #include "../pricing.h"

        struct order place_order(const char *item) {
            struct order o = { item, price_of(item) };
            return o;
        }
    ''',
    "src/pricing.h": "int price_of(const char *item);\n",
    "src/pricing.c": '''
        #include <string.h>
        #include "pricing.h"

        int price_of(const char *item) { return (int)strlen(item) * 100; }
    ''',
    "src/payments.h": "const char *charge(int amount);\n",
    "src/payments.c": '''
        #include "payments.h"

        static const char *gateway(int amount) { return "ok"; }

        const char *charge(int amount) { return gateway(amount); }
    ''',
}

CPP = {
    "include/shop/order_service.hpp": '''
        #pragma once
        #include <string>
        #include "shop/store.hpp"

        namespace shop {
        class OrderService {
        public:
            Order place(const std::string& item);
        private:
            Store store_;
        };
        }
    ''',
    "include/shop/store.hpp": '''
        #pragma once
        #include <string>
        #include <vector>

        namespace shop {
        struct Order { std::string item; int total; };
        class Store {
        public:
            void save(const Order& o);
        private:
            std::vector<Order> items_;
        };
        int price(const std::string& item);
        }
    ''',
    "src/order_service.cpp": '''
        #include "shop/order_service.hpp"

        namespace shop {
        Order OrderService::place(const std::string& item) {
            Order o{item, price(item)};
            store_.save(o);
            return o;
        }
        }
    ''',
    "src/store.cpp": '''
        #include "shop/store.hpp"

        namespace shop {
        void Store::save(const Order& o) { items_.push_back(o); }
        int price(const std::string& item) { return static_cast<int>(item.size()) * 100; }
        }
    ''',
    "src/main.cpp": '''
        #include <iostream>
        #include "shop/order_service.hpp"

        int main() {
            shop::OrderService svc;
            auto o = svc.place("book");
            std::cout << o.total << std::endl;
            return 0;
        }
    ''',
}

CSHARP = {
    "Shop/Program.cs": '''
        using Shop.Orders;
        using Shop.Payments;

        namespace Shop
        {
            public static class Program
            {
                public static void Main(string[] args)
                {
                    var svc = new OrderService();
                    var o = svc.Place("book");
                    System.Console.WriteLine(Gateway.Charge(o.Total));
                }
            }
        }
    ''',
    "Shop/Orders/OrderService.cs": '''
        namespace Shop.Orders
        {
            public class OrderService
            {
                private readonly OrderStore _store = new OrderStore();

                public Order Place(string item)
                {
                    var o = new Order(item, Pricing.Price(item));
                    _store.Save(o);
                    return o;
                }
            }
        }
    ''',
    "Shop/Orders/Order.cs": '''
        namespace Shop.Orders
        {
            public record Order(string Item, int Total);
        }
    ''',
    "Shop/Orders/OrderStore.cs": '''
        using System.Collections.Generic;

        namespace Shop.Orders
        {
            internal class OrderStore
            {
                private readonly List<Order> _items = new();
                public void Save(Order o) => _items.Add(o);
            }
        }
    ''',
    "Shop/Orders/Pricing.cs": '''
        namespace Shop.Orders
        {
            internal static class Pricing
            {
                public static int Price(string item) => item.Length * 100;
            }
        }
    ''',
    "Shop/Payments/Gateway.cs": '''
        namespace Shop.Payments
        {
            public static class Gateway
            {
                public static string Charge(int amount) => Send(amount);
                private static string Send(int amount) => "ok";
            }
        }
    ''',
}
