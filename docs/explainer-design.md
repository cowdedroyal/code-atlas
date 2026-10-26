# 讲解卡（Explainer Card）：把"读代码时的疑问"沉淀进地图

> 状态：P0（新仓库的套件与 lint/pack）和 P1（接收端：存储、`card` 命令、`build` 汇总、详情面板分组、沙箱阅读层）已实现；P2 起尚未做。与下文初稿不同之处见各节标注“实现时改为”。涉及两个交付物：一个新仓库（暂名 `atlas-explainer`：skill + 组件套件 + 格式规范 + 示例），
> 以及 Code Atlas 自身的一小块接收端（`.codemap/explainers/` 的读取、校验、构建、展示）。

## 1. 要解决的问题

现有语义层是 agent **主动**写的：划区域、写简短注释、画逻辑联系、录导览。它回答的是"这个系统是什么样的"。
但读者真正卡住的地方往往在别处：看 `meshvl` 的数据流水线时，会冒出"parquet 里到底存了什么""为什么起始数据是脏的、
要原地写回""stage1 的压缩只是换格式吗""`品类@标签` 长什么样"这类问题。这些问题：

- **是被需求拉出来的**：只有真有人读到那里、真卡住了才会出现，比 agent 想象读者会卡在哪里更准；
- **需要例子和图才讲得清**：200 字的 Markdown 注释装不下"一行数据怎样一路变形"；
- **聊完就丢**：问 code agent 得到了好解释，会话一关就没了，下一个人（或三个月后的自己）重新问一遍。

所以要做的事是：agent 回答这类问题时，除了在对话里答，还产出一张**可视化的讲解卡**，挂在地图上它所解释的那些文件/符号/区域上；
卡片进 git，随 `.codemap/` 一起推到远端；之后任何人 `codeatlas serve`，点到那个文件就能看到当年的疑问和解释。

这同时给了一个副产品：**哪里的卡片多，哪里就是代码自解释性差的地方**，`agenda` 可以据此提示"这个区域被问了 5 次，值得补好注释或重构"。

## 2. 先定一个原则：卡片围绕"一个贯穿例子"，不是问答列表

用户那七个问题（parquet、脏数据、stage1 压缩、stage2 扫 S3、stage3 统计、stage4a 清洗……）看起来是七个问题，
其实是**同一条流水线上的同一批数据**在不同阶段的样子。如果每个问题生成一张独立卡片，读者得到的是七块碎片；
如果做成一张卡片，让**一行具体的样本数据**从 parquet 出发、依次经过各阶段，每一站展示"这一站进来什么、出去什么、为什么"，
七个问题自然变成七个章节，而且每个概念都在上下文里出现。

因此 skill 的第一条规则是：先找出这几个问题共用的那条主线和那个具体例子，再动笔。卡片登记它回答了哪些问题（`questions` 字段），
这样在笔记本里按问题原话也搜得到。

## 3. 一张卡片是什么

一张卡片是 `.codemap/explainers/<id>/` 下的一个目录：

```text
.codemap/explainers/dataset-pipeline-walkthrough/
  card.yaml      清单：谁、问了什么、挂在哪、何时写的
  index.html     卡片正文，自包含的单文件 HTML
```

`card.yaml`：

```yaml
id: dataset-pipeline-walkthrough
title: 一行样本怎样走完数据流水线
questions:                       # 读者的原话，用于搜索和去重
  - parquet 里存的是什么？
  - 为什么要修空品类、删体系外行，还原地写回？
  - stage2 扫 S3 的失败原因是什么？
summary: 以第 1 行样本为例，从 parquet 到清洗后的规范短语，逐站展示数据形态与原因。   # ≤ 200 字，列表里显示
anchors:                         # 解释了哪些东西；校验时必须存在于当前索引
  - tools/dataset_pipeline/pipeline/stage0_clean.py
  - tools/dataset_pipeline/pipeline/stage2_scan_s3.py#scan_one
  - region:dataset-pipeline
tags: [pipeline, data]
language: zh
run: run-20261026-153012          # 与现有 changeset 的 run 记录同源，可回滚
at: 2026-10-26T15:30:12
```

**实现时改为：** `.codemap/explainers/<id>/index.html` 存的是作者写的源文件（套件用占位符引用），`build` 时才内联套件和 CSP，所以 git diff 里只有卡片自己的内容，不会每张卡片都带一份 30 KB 的套件。哈希写在 `card.yaml` 的 `anchor_hashes` 里。

写入时由程序给每个 anchor 记下当时的内容哈希，沿用现有的过期机制：代码变了，卡片仍然显示，但带"代码已变更，待复核"标记；
复核后用与 note 一致的 `confirm` 操作刷新。**不新增一套过期逻辑。**

## 4. 为什么载体选 HTML，而不是更多 Markdown 或自定义 JSON 块

现有查看器渲染 Markdown，但原始 HTML 按字面显示、图片只显示链接、不渲染数学和 Mermaid。这对"注释"够用，对"讲解"不够：
要展示一张真实表格的前三行、一个能逐步点击的阶段条、修复前后的对比，这些都需要排版和一点点交互。

三个候选：

| 方案 | 好处 | 代价 |
|---|---|---|
| 扩展 Markdown（新增 `flow`、`table` 之类围栏块，由查看器原生渲染） | 主题、深色模式、i18n 天然统一；安全；diff 友好 | 每种新图表都要改查看器；agent 表达力被词表卡死；与现有"Markdown 无 Mermaid"限制相冲突 |
| 自定义 JSON 块描述 | 同上，且可校验 | 同上，还更难读 |
| **沙箱 iframe 里放自包含 HTML（推荐）** | agent 最擅长；表达力不设限；查看器只需写一个"载入器"；卡片脱离 Atlas 也能单独打开 | 样式要靠套件统一；需要处理安全与主题同步 |

推荐第三种，但**用套件把自由度收回来**：agent 不写裸 HTML/CSS，而是用套件提供的一组语义标签。这样既保留表达力，
又让所有卡片长得一致、深浅色自动适配、体积小、lint 容易。

## 5. 组件套件（`kit`，v1）

套件是一份 CSS 加一小段 JS（自定义元素，无依赖，目标 ≤ 30 KB），提供讲解"一条数据怎么走"所需的最小词汇：

| 标签 | 用途 | 典型例子 |
|---|---|---|
| `<ex-table source="real\|illustrative">` | 展示数据样例；**必须声明样例来源** | parquet 的真实 schema 与前 3 行；`real` 显示"取自 xxx，读取于 yyy"，`illustrative` 显示醒目的"示意数据" |
| `<ex-flow>` / `<ex-stage>` | 阶段条：每站输入、输出、一句原因，可点击展开 | 流水线 stage0 → 4a |
| `<ex-compare before after>` | 同一数据处理前后的并排对比，自动高亮差异 | `品类@标签` 清洗前后 |
| `<ex-branch>` | 成功/失败分支及计数 | stage2 扫 S3：命中多少、各类失败原因各多少 |
| `<ex-term>` | 术语行内解释，悬停显示 | "COS""FBX 标注" |
| `<ex-code ref="path:start-end">` | 引用真实代码片段，带"在地图中打开"按钮 | 指向做清洗的那几行 |
| `<ex-qa>` | 一个问题与它的回答，作为章节单元 | 每个读者原问题一个 |

两条设计约束值得单独说：

- **样例必须有来源。** 讲解最危险的失败是 agent 编出一个看起来合理的假样本，读者当真。所以 `ex-table` 强制 `source`：
  `real` 要求 agent 确实读过数据（skill 里写明：用只读命令，如 `pyarrow` 读 parquet 头部，或读日志里的真实计数，并在卡片里记录来源与时间），
  `illustrative` 会被套件渲染成带底色的"示意"。lint 会拒绝缺少声明的表格。
- **卡片里的代码引用是链接而不是复制。** `ex-code ref` 在构建时从索引取源码快照并带上哈希，与 anchor 同样参与过期判断；
  点"在地图中打开"，卡片通过 `postMessage` 让外层地图飞到对应节点。

## 6. 查看器怎样接收卡片

接收端对卡片保持"不透明"：只认 `card.yaml` 和 `index.html`，不解析正文。这样套件迭代不会逼着查看器改。

**构建期**（`codeatlas build`）：
- 校验每张卡片（见 §7），把清单汇总进 `graph.json` 的 `explainers` 段，卡片目录复制到 `site/data/explainers/<id>/`；
- 每张卡片按 anchor 解析到地图节点，并带上过期状态。

**运行期**：
- 点选任一文件/符号/区域，详情面板多一个"讲解 (N)"分组，列出挂在这里的卡片标题与摘要（文件也列出挂在它的符号上的卡片；符号也列出挂在它所属类或函数上的卡片）；
- 点开卡片后在**宽阅读层**里显示（详情面板太窄，卡片需要约 720 px），底下是 `sandbox="allow-scripts"` 的 iframe；
- 顶栏新增"笔记本"入口：按区域分组、按时间排序，对**标题、问题原话、摘要、标签**做搜索；过期卡片带标记；
- 卡片里的 `atlas://file/<path>#<symbol>` 链接通过消息通道让地图定位节点；
- 外层通过 `postMessage` 把主题（浅/深）、语言、字号倍率发给 iframe，套件据此切换 CSS 变量；iframe 回报内容高度以便自适应。

**安全**：卡片经 git 在人与人之间流转，所以必须当作不可信内容。iframe 不给 `allow-same-origin`，页面带
`Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:`，
即没有网络、没有存储、碰不到外层 DOM。构建期 lint 再拒绝外部 URL、`fetch`/`XMLHttpRequest`、`eval`，以及超过大小上限（建议单卡 ≤ 200 KB）的卡片。

**远端流转**：因为卡片在 `.codemap/` 里并随它提交，"上传到远端"就是普通的 `git push`；换一台机器拉下来 `codeatlas serve` 即可看到。
`build` 生成的静态站点同样带着卡片，可直接发布到 Pages。

## 7. agent 接口：skill 与命令

skill 名为 `explain`（给 Claude Code、Codex 等用，和现有 `codeatlas` skill 并列安装）。接口沿用 changeset 契约，不让 agent 直接改文件：

```bash
codeatlas card search "<关键词或问题>"       # 先查有没有已有的卡片可更新，避免重复
codeatlas card validate -f card-dir/         # 校验：anchor 存在、套件合规、无外部请求、大小、样例来源声明
codeatlas card apply -f card-dir/            # 幂等写入 .codemap/explainers/，记入 log，可 revert
codeatlas card confirm <id>                  # 复核过期卡片，刷新 anchor 哈希
```

skill 要求 agent 按这个顺序工作：

1. **判断值不值得沉淀。** 一句话能答完的不做卡片；需要例子、图，或预计别人也会问的才做。回答里始终先给对话版的直接答案，卡片是补充。
2. **定位。** 用 `show`、`context`、`path` 找到问题所涉及的节点，读代码本身，别只看名字。
3. **取真样例。** 能读真实数据就读，记下来源；不能读就明确标 `illustrative`，不要伪装成真实。
4. **找主线。** 把读者的多个问题归到一条主线和一个贯穿例子（§2）。
5. **查重。** `card search` 命中同主题的旧卡，就更新它（同 `id` upsert），不要新开一张。
6. **写、校验、应用。** 在草稿目录写卡，`validate` 报错就改内容，不要为了通过而删来源声明。
7. **回话。** 用两三句话告诉用户卡片标题、挂在哪些节点上、怎么打开（`codeatlas serve` 的地址）。

沿用现有 skill 的硬规则：用读者的语言写（读 `config.yaml` 的 `language`）；不改源码和结构索引；卡片只解释，不写需要读者操作的指令。

## 8. 两个仓库怎么分工

用户想把这件事放进新仓库，大方向合理：套件和 skill 会独立演进，并且应该能脱离 Atlas 在浏览器里单独预览。但有一处必须说清楚：
**接收端只能在 Code Atlas 里**，因为 anchor 校验、过期判断、构建、查看器 UI 都依赖它的索引和前端。所以分工是：

| 在新仓库 `atlas-explainer` | 在 `code-atlas` |
|---|---|
| 格式规范：`card.yaml` 的 JSON Schema、`kit` 版本号约定 | 读取与写入：`semantic/explainers.py`，纳入 `snapshot`/`restore`/`log`，使其可回滚 |
| 套件源码（CSS + 自定义元素）与浏览器内回归测试 | `card validate/apply/search/confirm` 命令；`build` 时汇总与复制 |
| `explain` skill 及其参考文档 | 查看器：详情面板分组、阅读层、笔记本、主题消息通道 |
| 示例卡片（含用 `meshvl` 数据流水线写的那张）与独立预览页 | 把套件和 skill 随 wheel 一起打包（同现有 `skill install` 的做法） |

两边靠**格式版本号**耦合：`card.yaml` 带 `kit: 1`，查看器声明自己支持的范围；新仓库升级套件但不破坏 v1，查看器无需同步发布。
**实现时改为：** 校验逻辑（lint、打包、Schema、套件）只在新仓库里写一份；Code Atlas 通过 `scripts/sync_explainer_kit.py` 把它们复制到 `src/code_atlas/explainer_kit/` 并提交（测试里有漂移检查）。这样 Code Atlas 的 CI 和发布不依赖另一个未发布的包；代价是改套件后要同步一次。锚点校验、过期判断、`build` 汇总和查看器仍在 Code Atlas。

## 9. 用 meshvl 那组问题做个验证

验证标准不是"界面能出来"，而是**那七个问题能不能被一张卡片讲清楚**。设想中的这张卡（具体内容必须由 agent 读完
`tools/dataset_pipeline/` 的代码和真实数据后再写，这里不预设答案）：

- 开头：一个 `ex-table source=real`，取 parquet 的 schema 和前几行，回答"里面存的是什么"；
- 然后是 `ex-flow`，每一站配同一行样本的"进来/出去"：
  修空品类、删体系外行的 `ex-compare`（用一行真实的脏数据）；
  stage1 压缩的对比，并用真实文件大小或行数说明压缩了什么、为什么；
  stage2 扫 S3 的 `ex-branch`，用日志里的真实计数展示命中与各类失败；
  stage3 统计的是什么 label、统计结果是什么形态；
  stage4a 把 `品类@标签` 清洗成规范短语的前后对比；
- 全卡 anchor 到那几个 stage 脚本，`questions` 里登记读者原话。

通过标准：把卡片给一个没读过这个仓库的人看，他能不翻代码复述出这条流水线；并且所有 `real` 样例都能追溯到来源。

## 10. 分阶段落地

| 阶段 | 内容 | 完成标志 |
|---|---|---|
| P0 | 新仓库骨架；`card.yaml` Schema；套件 v1 的最小子集（`ex-table`、`ex-flow`、`ex-compare`、`ex-qa`）；独立预览页 | 手写一张卡能在浏览器里看，深浅色都对 |
| P1 | Code Atlas 接收端：存储、`card validate/apply`、`build` 汇总、详情面板分组、沙箱阅读层 | 手写的卡片挂在 `meshvl` 的某文件上，点开能读，改代码后出现过期标记 |
| P2 | `explain` skill；用它在 `meshvl` 上真实回答那七个问题，产出 §9 那张卡 | 人工按 §9 标准验收 |
| P3 | 笔记本入口与搜索；`agenda` 的"热点"提示；`ex-branch`、`ex-term`、`ex-code`；导览步骤可链接卡片 | 在两个以上仓库上跑过 |

## 11. 风险与待决

- **HTML 的 diff 不友好。** 缓解办法是套件把标签做得语义化、正文里少写样式，卡片体积小；同时每次 `apply` 在 `log.jsonl` 里留一行摘要，评审时先看摘要。
- **agent 偷懒编样例。** 靠 `source` 强制声明加 skill 规则约束，不能完全杜绝；人工评审时 `real` 标签是第一检查点。
- **套件 v1 词汇不够。** 允许卡片里用 `<ex-raw>` 逃生舱写自定义 SVG/HTML，但 lint 对它更严，且它不享受深浅色自动适配。
- **脚本交互的边界（P0 已定）。** 卡片作者不能写自己的脚本，页面里唯一的 `<script>` 是套件本身（展开/折叠、差异高亮、消息通道），
  lint 拒绝其他脚本和事件属性；沙箱与 CSP 仍然兜底。需要更自由的交互时再引入 `ex-raw` 逃生舱。
- **待决：`questions` 里是否保留提问者标识？** 本稿不记录，避免把个人信息写进仓库；如需要协作归属，应该由用户自己决定写什么。
