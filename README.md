# 年代约束审计 API

供地质记录整理使用的年代约束审计与偏好拟合服务（Python + FastAPI）。

提交：

- **地层列表**：每层有唯一编号 `id`，以及允许的**整数**年代区间
  `[youngest_ma, oldest_ma]`，单位**百万年（Ma）**，数值越大越古老
  （故 `youngest_ma <= oldest_ma`）。
- **观测关系**：`older_id` 比 `younger_id` 古老（“甲比乙古老”）。

服务把每条关系转换为整数约束：

```
age(older_id) >= age(younger_id) + 1
```

即在关系图上加入一条 `older -> younger` 的有向边。两个接口：

- **POST /audit** —— 约束审计：检测关系环、双向传播收紧可行年代区间，
  可行时返回古老 → 年轻方向上编号字典序最小的合法顺序；
- **POST /fit** —— 偏好拟合：另接收每层一项偏好（参考年代 + 权重），
  在相同约束下选取整数年代，最小化 `max(weight × |age − target_ma|)`；
- **POST /gap** —— 年代差极值：另收两个查询编号 `a_id`、`b_id`，在全体
  合法赋值上求 `age(a_id) − age(b_id)` 的最小值、最大值，并各返回一个
  达到边界的全体地层年代映射。

无数据库、无外部服务，单容器即可运行。

## 一条命令启动

前置条件：已安装 Docker（含 Compose 插件，`docker compose version` 可用），
无需自行准备 Python 环境。

```bash
docker compose up --build
```

依赖安装、单元测试与服务初始化都在这一命令内完成：构建镜像时安装
`requirements.txt` 并运行全部单元测试（失败即中止构建），随后启动
uvicorn 并通过 `/health` 健康检查。服务在前台运行，`Ctrl+C` 停止；
后台运行可用 `docker compose up --build -d`，`docker compose down` 拆除。

启动后 API 位于本机 **http://localhost:8000** ：

| 地址 | 说明 |
| --- | --- |
| http://localhost:8000/audit | 约束审计接口（POST） |
| http://localhost:8000/fit | 偏好拟合接口（POST） |
| http://localhost:8000/gap | 年代差极值接口（POST） |
| http://localhost:8000/docs | Swagger 交互文档 |
| http://localhost:8000/health | 健康检查 |

## 配置项

应用本身无需任何配置（无数据库、无外部密钥）；唯一可调的是宿主机映射端口。

| 配置 | 位置 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `HOST_PORT` | `.env` 或环境变量 / Compose 变量 | `8000` | 映射到宿主机的端口；容器内固定监听 `8000` |

如需改用宿主机 18080 端口：

```bash
HOST_PORT=18080 docker compose up --build
```

也可以复制 `.env.example` 为 `.env` 后修改 `HOST_PORT`。

## POST /audit：约束审计

1. 同一地层比自身古老（自环）、编号重复、区间非法、引用未提交的编号等
   情况返回 **HTTP 422** 并附带中文错误说明；
2. 检测关系**环**，命中时返回一条首尾编号相同的成环路径 `cycle_path`；
3. 无环时沿关系图双向传播，收紧每层最早/最晚可行年代：
   - 最晚（上）界沿拓扑正向收紧：`U(v) = min(U(v), U(u) - 1)`；
   - 最早（下）界沿拓扑反向收紧：`L(u) = max(L(u), L(v) + 1)`；
4. 若某层收紧后 `L > U`（区间被压空），返回冲突地层编号 `conflicting_strata`
   与全部收紧区间 `tight_intervals`；
5. 可行时返回各层收紧后的区间 `tight_intervals`，以及**古老 → 年轻**
   方向上编号**字典序最小**的合法顺序 `oldest_to_youngest`（Kahn 算法 +
   最小堆）。

### 请求示例

```bash
curl -s -X POST http://localhost:8000/audit \
  -H 'Content-Type: application/json' \
  -d '{
    "strata": [
      {"id": "A", "youngest_ma": 100, "oldest_ma": 110},
      {"id": "B", "youngest_ma": 100, "oldest_ma": 110},
      {"id": "C", "youngest_ma": 100, "oldest_ma": 110}
    ],
    "relations": [
      {"older_id": "A", "younger_id": "B"},
      {"older_id": "B", "younger_id": "C"}
    ]
  }'
```

可行时响应（沿 `A -> B -> C` 双向传播后，每层区间都被收紧）：

```json
{
  "status": "feasible",
  "tight_intervals": {
    "A": {"youngest_ma": 102, "oldest_ma": 110},
    "B": {"youngest_ma": 101, "oldest_ma": 109},
    "C": {"youngest_ma": 100, "oldest_ma": 108}
  },
  "oldest_to_youngest": ["A", "B", "C"]
}
```

其他两种结果：

- 成环：`{"status": "cycle", "cycle_path": ["A", "B", "C", "A"]}`，
  路径首尾编号相同，代表环从 `A` 出发回到 `A`；
- 冲突：`{"status": "conflict", "conflicting_strata": ["B", ...],
  "tight_intervals": {...}}`，冲突层的区间形如
  `{"youngest_ma": 51, "oldest_ma": 49}`（下界超过上界，即被压空）。

## POST /fit：偏好拟合

在 /audit 相同的约束（整数年代区间 + `age(older) >= age(younger) + 1`）
下为每层选取整数年代，最小化最大加权偏差：

```
minimize  max_i( weight_i × |age_i − target_i| )
```

请求在 `strata`、`relations` 之外增加 `preferences`，每项为
`{"id", "target_ma", "weight"}`：

- **每层恰好一项偏好**：缺漏、重复或引用未提交编号均返回 **HTTP 422**；
- `target_ma` 为整数，`weight` 为正整数；
- 规模与数值上限（越界返回 **HTTP 422**）：最多 **200 层**、
  **1000 条关系**；年代与参考年代绝对值 ≤ **10^12**；权重 ≤ **10^6**。

响应（由 `status` 区分）：

- 原约束无解时与 /audit 相同：`cycle` / `conflict`；
- 可行时返回 `optimal`，附最优值 `optimal_value` 与各层年代映射
  `ages`；**多解时取按编号升序排列的年代向量中字典序最小者**。

求解不逐年代枚举：对候选阈值 T，`weight × |age − target| <= T`
等价于整数区间 `[target − ⌊T/weight⌋, target + ⌊T/weight⌋]`，与原区间
取交后在关系 DAG 上传播即可判定可行性；目标值单调可行，二分最小 T
后按编号升序贪心固定各层最小可行年代。单次请求为
`O((层数 + 关系数) × (log 值域 + 层数))`，200 层 / 1000 关系约 0.2 秒。

### 请求示例

```bash
curl -s -X POST http://localhost:8000/fit \
  -H 'Content-Type: application/json' \
  -d '{
    "strata": [
      {"id": "A", "youngest_ma": 0, "oldest_ma": 10},
      {"id": "B", "youngest_ma": 0, "oldest_ma": 10}
    ],
    "relations": [
      {"older_id": "A", "younger_id": "B"}
    ],
    "preferences": [
      {"id": "A", "target_ma": 0, "weight": 1},
      {"id": "B", "target_ma": 10, "weight": 1}
    ]
  }'
```

响应（`A >= B + 1` 使两个目标不可兼得，最优值 6；多解 `(5,4)`/`(6,5)`
中取编号升序向量的字典序最小者）：

```json
{
  "status": "optimal",
  "optimal_value": 6,
  "ages": {"A": 5, "B": 4}
}
```

## POST /gap：年代差极值与边界见证

在 /audit 相同的约束（整数年代区间 + `age(older) >= age(younger) + 1`）
下，给定两个已提交编号 `a_id`、`b_id`，在**全体合法整数赋值**上求

```
gap = age(a_id) − age(b_id)
```

的最小值 `min_gap` 与最大值 `max_gap`，并分别返回一个达到该边界的
**全体地层年代映射** `min_gap_ages`、`max_gap_ages`（任意合法见证即可）。

请求与限制：

- 请求体沿用 /audit 的 `strata`、`relations`，另加 `a_id`、`b_id` 两个
  查询编号；查询编号未提交（或为空）返回 **HTTP 422**；
- 规模与数值上限（越界返回 **HTTP 422**）：最多 **200 层**、
  **1000 条关系**；区间端点绝对值 ≤ **10^12**；
- **同层查询**（`a_id == b_id`）两个极值都为 **0**；
- **互不连通的地层**同样支持：没有关系链时极值由各自区间决定；
- 原约束无解时与 /audit 相同，返回 `cycle` / `conflict`。

### 算法（不逐年代枚举，也不直接相减独立区间端点）

这是一个差分约束系统。先用 /audit 的 DAG 双向传播得到每层收紧后的可行
区间 `[L, U]`；再令 `d(x, y)` 为关系图上 `x → y` 的最长路（边权 1，
不可达忽略），即关系链强制的 `age(y) − age(x) ≥ d(x,y)`，则

```
min_gap = max( L[a] − U[b],  d(a,b)  )     # a→b 不可达时只取前者
max_gap = min( U[a] − L[b], −d(b,a) )      # b→a 不可达时只取前者
```

单层区间端点之差只覆盖区间这一类约束；关系链的强制差必须同时参与取极
（例如存在 `b → a` 关系时，`max_gap` 被钉为负数）。边界见证通过把源点
固定在其传播下界、其余层取上界后沿拓扑正向传播得到，为 `O(层数 +
关系数)`，200 层 / 1000 关系为毫秒级。

### 请求示例

```bash
curl -s -X POST http://localhost:8000/gap \
  -H 'Content-Type: application/json' \
  -d '{
    "strata": [
      {"id": "A", "youngest_ma": 0, "oldest_ma": 10},
      {"id": "B", "youngest_ma": 0, "oldest_ma": 10},
      {"id": "C", "youngest_ma": 0, "oldest_ma": 10}
    ],
    "relations": [
      {"older_id": "A", "younger_id": "B"},
      {"older_id": "B", "younger_id": "C"}
    ],
    "a_id": "A",
    "b_id": "C"
  }'
```

响应（关系链强制 `age(A) − age(C) ≥ 2`，故 `min_gap = 2`；两个映射都是
满足全部约束、且恰取对应极值的全体年代赋值）：

```json
{
  "status": "gap",
  "a_id": "A",
  "b_id": "C",
  "min_gap": 2,
  "max_gap": 10,
  "min_gap_ages": {"A": 2, "B": 1, "C": 0},
  "max_gap_ages": {"A": 10, "B": 9, "C": 0}
}
```

查询编号未提交时返回 **HTTP 422**：

```json
{"detail": ["查询编号 a_id 引用了未提交的地层编号：'X'"]}
```

## 本地开发（不使用 Docker）

```bash
python -m pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
python -m unittest discover -s tests    # 运行测试
```

## 目录结构

```
app/
  audit.py     # 校验、成环检测、上下界传播、字典序最小拓扑序（三个求解接口共用）
  fit.py       # 偏好拟合：阈值二分 + 字典序最小最优年代选取
  gap.py       # 年代差极值：差分约束（DAG 最长路）+ 边界见证构造
  schemas.py   # Pydantic 请求/响应模型（含规模与数值上限）
  main.py      # FastAPI 路由（/audit、/fit、/gap、/health）
tests/
  test_audit.py  # /audit 核心逻辑
  test_fit.py    # /fit 核心逻辑（含暴力枚举交叉验证）
  test_gap.py    # /gap 核心逻辑（含暴力枚举交叉验证）
  test_api.py    # HTTP 接口层（需 fastapi/httpx，镜像构建期运行）
Dockerfile
docker-compose.yml
requirements.txt
```
