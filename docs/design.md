# Code Atlas：用一张可缩放的地图介绍大型代码仓库

> 本文保留最初的设计方案，部分实现方式已变化。当前使用方式以 README 和
> `src/code_atlas/skill/` 为准：现有查看器支持层级锁、按层级筛选连线、派生的模块作用域
> 与声明体折叠、按需语言高亮和 Markdown 预览。高亮使用 highlight.js 与 tree-sitter
> 回退数据；`build` 会增量扫描并缓存布局，`serve` 监听语义文件变化后重建，浏览器需刷新。
> 深色模式、Leiden 草稿细分和显式 confirm 已实现；布局和边捆绑的具体边界见
> `docs/validation.md`。下面的 Shiki、任意旋转对齐等描述属于设计目标，不代表当前实现。

## Context

接手一个大项目时，最难的不是读懂某个函数，而是**先搞清它整体怎么分块、块之间怎么协作、关键路径落在哪些文件和函数上**。README 和架构文档往往过时，IDE 又只能逐个文件看。

做 AI 知识地图（`D:\workspace\ai-knowledge-map`）时验证过一套做法：一张带语义缩放的地图、带理由的关系、由 agent 按 skill 写变更集。这个项目把它搬到代码上，做成一个**通用开源工具**（发布到 GitHub 和 PyPI），谁想快速了解自己或别人的仓库都能用。

和 kmap 有一个根本不同：代码的**结构可以由程序确定性地完整扫出来**，包括文件、类、函数、import、调用和继承。所以这里把职责一分为二：

- **结构层**：由程序负责，扫一遍仓库就保证完整，并且随代码更新，不需要 LLM。
- **语义层**：由 agent 加 skill 负责，包括逻辑区域、摘要、逻辑联系和关键流程。语义层挂在结构层的稳定 ID 上；代码一变，对应的标注自动标成过期，进入待办。

已确认的约束：通用开源项目；语义层存进**被分析仓库的 `.codemap/`**，可以提交、可以 review；第一个验证对象是 **`D:\workspace\artist_agent`**（7.5k 个跟踪文件，Python、TS、JS 混合，还提交了 node_modules 和数据集）。

名称为 **Code Atlas**：PyPI 包名改为 `repo-code-atlas`（`code-atlas` 被 PyPI 近似名称保护拒绝），CLI 命令 `codeatlas`，Python 模块 `code_atlas`。项目目录是 `D:\workspace\code-atlas`。

全文沿着**一段代码的一生**展开：扫描器发现它 → 给它一个稳定 ID → 解析出它和别处的结构联系 → agent 判断它属于哪个逻辑区域、承担什么职责 → 布局给它分配位置 → 它出现在地图的某个缩放层上 → 代码改了，程序察觉并让 agent 复核。

---

## 1. 地图长什么样：四个缩放层

| 层 | 用户看到什么 | 线代表什么 |
|---|---|---|
| **L0 区域** | 逻辑区域的疆域和名字，比如「HTTP 入口」「推理编排」「数据集管线」；区域可以嵌套，L0 只显示顶层 | 区域之间的逻辑联系。agent 写的联系画成带标签的线；结构依赖按区域聚合，画成粗细不同的弧线 |
| **L1 文件** | 疆域里出现点，每个点是一个文件，大小按重要度（PageRank）定 | 文件间依赖：import 加上聚合后的调用。默认只画悬停或选中文件的线，以及权重最高的前 N 条 |
| **L2 符号** | 文件点展开成矩形框，框里是类（类框里再套方法）、函数、常量和路由等，按源码顺序排 | 符号间的调用、继承、实现和实例化 |
| **L3 细节** | 选中一个符号：右侧面板显示代码（带高亮）、摘要、调用方和被调方，以及所在流程 | 一到两跳内的邻居，按关系类型分组 |

另外有一个**流程（Tour）**视图：agent 写好的端到端路径，例如「一次推理请求的一生」，在地图上是一串按顺序点亮的符号，可以逐步播放。这是「快速了解项目」最直接的入口。

---

## 2. 结构层：怎样支持大部分代码

### 2.1 文件发现

- 优先用 `git ls-files` 加工作区里未跟踪、但没被 ignore 的文件；非 git 目录退回到遍历目录并读 `.gitignore`。另外支持 `.codemapignore`。
- **分类**：用路径规则、文件头标记和大小阈值，把文件标为 `source | test | config | docs | generated | vendored | data | binary`。规则类似 GitHub linguist，例如 `node_modules/`、`dist/`、`*.min.js`、`*.map`、lockfile、`@generated` 头、超过 1MB 的文件。
- vendored、generated、data 和 binary 文件不解析符号，在地图上折叠成「依赖或资产」块。artist_agent 正好用来测这一点：它有 3986 个 node_modules 文件和 987 个 `.map` 文件。

### 2.2 解析：tree-sitter 加分级语言支持

用 `tree-sitter` 0.26 和 `tree-sitter-language-pack` 1.22（一个 wheel 内含约 160 种语法，Windows、macOS、Linux 都有预编译包）。语言按支持程度分三级：

1. **一级语言**：自己写查询包 `queries/<lang>/{defs,refs,imports}.scm`，能拿到嵌套结构、签名、调用、继承和 import。首批是 Python、TypeScript/TSX、JavaScript；然后是 Go、Java、Rust、C/C++、C#、Kotlin、Ruby、PHP、Swift。查询写法参考各语法自带的 `tags.scm` 和 Aider 的 repo-map 查询。
2. **二级语言**：有语法但没有专门的查询包。先用语法自带的 `tags.scm`；如果没有，就按节点类型名的通用规则提取，例如 `*_definition`、`*_declaration`、`class_*`、`function_*`。这一级只能拿到定义和粗略的调用。
3. **三级语言**：没有语法，只作为文件节点出现，不解析符号。

非代码文件也提取「符号」，这样配置和文档同样能上地图：Markdown 取标题，YAML/JSON/TOML 取顶层键，SQL 取表和视图，Dockerfile 取 stage，proto 取 message 和 service，shell 取函数。

**符号模型**：`kind`（module、class、interface、function、method、variable、constant、type、route、table……）、限定名、源码范围、签名、父符号（组成包含树）、**规范化正文哈希**（去掉注释和空白后计算，用来识别改动和移动）。

### 2.3 引用解析：最难的部分，分两档精度

- **默认档：启发式，零配置。**
  - **import**：按语言写解析器，把 import 映射到文件，可信度高。例如 Python 的模块路径和包、相对导入；JS/TS 的相对路径、`tsconfig` 的 paths 和 index 文件；Go 的包目录；Java 的包路径；C 的 include 路径。
  - **调用和引用**：按名字逐级解析：局部作用域 → 同文件 → 显式导入的名字 → 导入模块的成员 → 同包内唯一名 → 全局唯一名。每条边记下解析方式和可信度。候选超过 k 个时不连线，只计入统计，宁缺毋滥。
- **精确档：可选。** 如果用户自己跑过 SCIP 索引器（scip-python、scip-typescript、scip-java、scip-go、rust-analyzer、scip-clang），就用 `codeatlas scan --scip index.scip` 导入，按语言覆盖启发式的引用边。

### 2.4 稳定 ID 与增量扫描

- **ID 格式**：`<path>#<限定名>`。同名重载加签名后缀，再冲突就加序号。路径统一用 POSIX 风格的相对路径，避免 Windows 和 Linux 生成的 ID 不一致。
- **改名和移动**：结合 `git diff -M` 的重命名检测和正文哈希匹配，旧 ID 记进 alias 表，语义标注跟着迁过去，不会丢。
- **增量**：索引里记录上次扫描的 commit 和每个文件的内容哈希。重扫时只解析变化的文件，再对这些文件和**反向依赖它们的文件**重新解析引用。解析用进程池并行。目标：artist_agent 过滤后的源码全量扫描在 60 秒内，改一个文件后的增量扫描在 3 秒内。
- 结构索引是**可重建的缓存**，存在 `.codemap/cache/index.sqlite`，加进 gitignore。

---

## 3. 语义层：按第一性原理的开放分类

### 3.1 逻辑区域

区域的名字是**开放**的，由 agent 根据具体项目起名。它的定义问题始终是：**「去掉这块，系统会失去哪项能力？」** 答案就是区域名和一句话职责，比如「把多视角图像转成推理分割样本」，而不是「utils」「core」这类目录名。

为了在不同项目之间有统一的配色和图例，每个区域还要从一组**封闭的基本角色**里选一个。这组角色来自所有程序共有的信息流：接收输入 → 决策和变换 → 持有状态 → 产生外部效果，再加上支撑和元层两类：

| 角色 | 回答的问题 |
|---|---|
| `interface` 入口 | 外部世界从哪里进来（CLI、HTTP、UI 事件、消息） |
| `orchestration` 编排 | 谁把步骤串成流程 |
| `core` 核心规则 | 不依赖外部世界的业务规则和算法 |
| `state` 状态与数据 | 数据长什么样、存在哪（模型、schema、存储） |
| `integration` 外部适配 | 系统去调用谁（第三方 API、SDK、设备、模型服务） |
| `presentation` 呈现 | 给人看的部分 |
| `foundation` 支撑 | 被大家共用的基础设施（配置、日志、通用工具） |
| `tooling` 元层 | 不进运行时的部分（构建、测试、部署、脚本） |

区域组成一棵树：一个文件只有一个**主区域**，用来决定它在地图上的位置；可以再有若干**次区域**，用来高亮和筛选。

### 3.2 逻辑联系

结构边由程序产生，类型封闭：`contains`、`imports`、`calls`、`inherits`、`implements`、`instantiates`、`references`、`tests`。

语义边由 agent 写。形式是**开放标签加封闭族**，和 kmap「每条边都有理由」的规则一脉相承：

- `label`：开放，用自然语言写，例如「把渲染任务投递到队列」。
- `family`：封闭，只有五个基本族：`control`（触发、调用、调度）、`data`（产出、消费、转换）、`contract`（实现、扩展、约束）、`config`（配置、注册、装配）、`observe`（监控、日志、校验）。
- `rationale`：一句话依据。
- `evidence`：**必须指向存在的符号 ID 或代码行范围**，校验器会检查。这相当于 kmap 里「没有来源的边不能入库」那条规则。

语义边可以连在区域、文件和符号任意两个层级之间。L0 上画的就是区域之间的语义边。

### 3.3 摘要和流程

- **摘要**：给区域、文件和重要符号写。重要度用结构图上的 PageRank 排序，agent 按预算从最重要的开始写，不追求全覆盖；`status` 会报告覆盖率。
- **流程**：一个有名字的有序符号序列，每一步附一句说明，并且必须能在结构边上找到对应的调用链（校验器检查相邻两步之间存在路径）。

### 3.4 agent 介入之前，地图也是完整的

`codeatlas scan` 会先按目录树加 Leiden 社区发现（在文件依赖图上跑，用 igraph）自动生成**草稿区域**，并标记为 `auto`。所以第一次扫描完就能浏览。agent 的第一件事是把草稿区域改写成按职责划分的区域。

### 3.5 存储：`.codemap/` 目录，适合用 diff 查看

```
.codemap/
  config.yaml              语言、忽略规则、摘要使用的语言（zh/en）
  regions.yaml             区域树：id、name、role、responsibility、parent
  assign.yaml              文件或目录前缀 → 主区域/次区域（支持 glob，新文件自动归属）
  notes/<shard>.jsonl      摘要：{id, text, anchor, run}；区域说明在 _regions.jsonl，文件和符号按顶层目录分片（不随区域调整而搬家）
  relations.jsonl          语义边
  tours/<name>.yaml        流程
  cache/                   （gitignore）index.sqlite、layout.json、构建出的站点
```

所有文件都按键稳定排序，一行一条记录，PR 里的 diff 才看得清。每条标注记下写它时目标的 `anchor_hash`；扫描时哈希变了，这条标注就判为**过期**。过期的标注仍然显示，只是加一个「待复核」标记。

---

## 4. agent 接口：CLI 加 skill

agent 不直接改 `.codemap/` 里的文件，而是沿用 kmap 的契约：**写变更集 → `validate` → `apply`**。`apply` 是幂等的，按 run 记录日志，可以回滚。CLI 的输出都是 JSON：

| 命令 | 作用 |
|---|---|
| `codeatlas scan [--full] [--scip f]` | 扫描或增量扫描结构层，刷新过期标记，重排新出现的部分 |
| `codeatlas status` | 统计规模、各语言支持级别、语义覆盖率、过期条数 |
| `codeatlas agenda` | 现算待办：`regionize`（还有 auto 区域）、`annotate`（重要但没摘要）、`refresh`（过期）、`link`（区域间结构依赖很强，但还没有语义边）、`tour`（入口符号还没有流程） |
| `codeatlas show <id> [--code] [--depth N]` | 节点详情、代码片段和邻居，给 agent 读代码用 |
| `codeatlas context <region>` | 区域的成员、对外依赖和最重要的符号，给 agent 划区和写摘要用 |
| `codeatlas validate/apply --file cs.json` | 校验和落库语义变更 |
| `codeatlas serve` / `codeatlas build` | 本机浏览 / 构建静态站点 |
| `codeatlas skill install [--target claude|agents]` | 把 skill 装到 `~/.claude/skills` 或 `.agents/skills` |

skill 放在 `skill/codeatlas/`，遵循开放的 Agent Skills 格式，所以 Claude Code、Codex 等都能用。结构仿照 `ai-knowledge-map/skill/kmap/`：

- `SKILL.md`：入口，包括调用方式、各模式和硬规则。
- `references/`：
  - `regions.md`：怎样按「去掉它失去什么能力」划区，八种角色的判据和正反例。
  - `relations.md`：五个族的判据，以及好标签和坏标签的对照。
  - `writing.md`：摘要写法，回答「它解决什么问题 → 怎么做 → 代价或边界」，有长度上限。
  - `tours.md`：怎么挑入口、流程该停在哪一层。
  - `workflows.md`：init、annotate、link、tour、refresh、auto 六个流程。

**硬规则**包括：每条语义边都要有 evidence；摘要不复述代码；一个待办最多读 N 个文件；不改结构层。

---

## 5. 布局与渲染

**布局**由 Python 预计算，前端只负责渲染，这样地图稳定，用户能记住「哪块在哪」。计算方式是自底向上算尺寸，再自顶向下放位置：

1. **文件内**：符号框按源码顺序做网格装箱，类框里再装方法。这样算出每个文件的矩形尺寸，也就是它的 **footprint**。
2. **区域内**：文件按区域内的依赖做力导向布局，碰撞半径取 footprint，保证放大到 L2 时矩形不重叠。
3. **区域之间**：用带区域图吸引力的装箱布局排开子区域，再逐级向上。
4. **稳定性**：增量时老位置不动，新文件放在它的依赖邻居的质心附近；全量重排后用 Procrustes 对齐旧布局。这部分直接改写 `ai-knowledge-map/backend/kmap/services/layout.py` 里的 `place_new_nodes` 和对齐逻辑。

**前端**：Vite + React + TypeScript + **deck.gl**（OrthographicView）。不沿用 kmap 的 sigma.js，因为这里要画嵌套矩形、多边形疆域，还要按缩放切换层，deck.gl 的图层模型更合适，也能撑住百万级图元。

- 不同缩放层用不同的图层集合：疆域用 PolygonLayer，文件点用 ScatterplotLayer，符号框用 SolidPolygonLayer，线用 PathLayer 加边捆绑，文字用 TextLayer 并按重要度避让。
- **按视口分块加载**：L0/L1 的数据一次性拿到；L2 的符号和代码片段按区域分块，进入视口时再取，所以十万文件级的仓库也能浏览。
- 视觉沿用 kmap 的瑞士风格（`ai-knowledge-map/frontend/src/palette.ts`）。八种区域角色对应八个低饱和色；界面提供中英两种语言。
- 代码高亮用 Shiki。⌘K 搜索文件和符号，选中结果后地图飞到对应位置。

**API 全是静态 JSON**（`graph.json`、`regions.json`、`chunks/<region>.json`、`tours.json`）：

- `codeatlas serve`：先构建，再起一个本地静态服务器，并监听 `.codemap/` 的变化。
- `codeatlas build`：输出 `site/`，可以直接放到 GitHub Pages、Caddy 或任何静态托管上。
- 加 `--no-source` 可以不打包代码片段，用于不想公开源码的场合。

所以服务端不需要后端进程。前端在 CI 里构建好再打进 wheel，用户只要执行 `uvx repo-code-atlas` 或 `pipx install repo-code-atlas`，不用装 Node。

---

## 6. 仓库结构

```
D:\workspace\code-atlas\
  pyproject.toml            hatchling + uv；入口 codeatlas = code_atlas.cli:app（Typer）
  src/code_atlas/
    discover.py             文件发现与分类
    parse/                  tree-sitter 驱动、languages.py（扩展名→语法→支持级别）、queries/<lang>/*.scm
    resolve/                各语言 import 解析器 + 名字解析阶梯 + scip.py
    index.py                SQLite 结构索引（files、symbols、edges、aliases、scans）
    ids.py                  稳定 ID、正文哈希、改名追踪
    semantic/               .codemap 读写、changeset 校验与落库、过期检测、agenda
    cluster.py              草稿区域（目录树 + Leiden）
    layout.py               分层布局
    site.py                 静态站点构建
    web/                    （构建产物，CI 生成）
  frontend/                 Vite + React + TS + deck.gl
  skill/codeatlas/          SKILL.md + references/
  tests/fixtures/<lang>/    每种语言一个小仓库 + 期望的符号和边（快照测试）
  docs/design.md            即本方案
  README.md / LICENSE（Apache-2.0）/ .github/workflows（测试 + 构建前端 + 发布）
```

---

## 7. 里程碑

| 阶段 | 内容 | 完成标志 |
|---|---|---|
| 状态（2026-10-23） | M0–M5 已完成（M5：Go/Java/Rust/C/C++/C# 升为一级、SCIP 导入、分块与按需搜索索引、发布准备、自映射演示站；未实际发布到 PyPI 和 Pages）。Tour 的校验规则放宽为“每一步可从此前任一步到达” | |
| **M0** | 项目骨架、`uv` 环境、CI、`git init`；把本方案存为 `docs/design.md` | `codeatlas --help` 能跑；CI 通过 |
| **M1 结构层** | 文件发现与分类；Python、TS/TSX、JS 一级查询包；二级通用提取；非代码文件提取；import 解析与名字解析阶梯；稳定 ID；SQLite 索引；`scan`/`status`/`show` | fixture 快照测试通过；artist_agent 扫描时 node_modules 和 `.map` 被折叠，符号数和抽查的 20 条调用边正确 |
| **M2 地图** | 草稿区域；分层布局；deck.gl 前端，L0–L3 加搜索加详情面板；`serve`/`build` | 不经过 agent，artist_agent 就能缩放浏览；放大后矩形不重叠；在浏览器预览中截图核对 |
| **M3 语义层** | `.codemap/` 格式；changeset 的 validate/apply/回滚；agenda；skill v1；L0 语义边、摘要和过期标记的展示 | 在 artist_agent 上跑 `init` 加若干轮 `annotate`/`link`，区域名能回答「去掉它失去什么」 |
| **M4 增量与流程** | 增量扫描、改名追踪、过期检测；Tour 的校验与播放 | 改名一个函数后摘要跟着走；改函数体后摘要进入 refresh 待办；artist_agent 至少有 2 条流程能播放 |
| **M5 推广** | 其余一级语言；SCIP 导入；视口分块加载；中英界面；README 和演示站；发布到 PyPI | 在一个公开的大型仓库上跑通，演示站部署到 GitHub Pages |

---

## 8. 验证

- **单元和快照测试**（pytest）：
  - 每种一级语言用 fixture 仓库对比期望的符号树和边。
  - 名字解析阶梯在歧义时不连线。
  - ID 在 Windows 和 Linux 上一致。
  - 改名后通过 alias 找回标注；正文改动后标注判为过期。
  - changeset 拦截不存在的 evidence、跨族的非法边和超长摘要；`apply` 幂等。
  - 增量扫描的结果和全量扫描完全一致（同一个 commit 两种方式各跑一次，对比索引）。
- **端到端**（artist_agent）：
  1. `codeatlas scan`，检查 `status` 里的规模、语言分布和折叠情况。
  2. `codeatlas serve`，在浏览器面板逐层缩放、截图，确认 L1 到 L2 时文件点展开成矩形、线随层级切换。
  3. 用 skill 跑一轮 `init` 和 `annotate`，检查 `.codemap/` 下文件的 git diff 是否可读。
  4. 改动 artist_agent 的一个文件后增量扫描，确认 agenda 出现对应的 refresh 待办。
- **性能**：用合成的 5 万文件、50 万符号仓库测试扫描耗时和前端缩放帧率。
- **质量抽检**：随机抽 20 条调用边对照源码判断是否正确；抽 10 个区域，看名字和职责是否站得住。
