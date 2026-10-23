import { lazy, Suspense } from "react";
import type { Props } from "./MarkdownView";

const View = lazy(() => import("./MarkdownView"));
export default function Markdown(props: Props) {
  return <Suspense fallback={<div className="markdown-plain">{props.text}</div>}><View {...props} /></Suspense>;
}
