import { t } from "./i18n.ts";
export const symbolName = (symbol: { kind: string; name: string }) => symbol.kind === "module_scope" ? t("moduleScope") : symbol.name;
