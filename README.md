# Xianyu Sniper

一个本地运行的闲鱼捡漏原型，基于 `Python + Playwright + SQLite`，包含：

- 关键词配置
- 闲鱼搜索结果抓取
- 捡漏分数计算
- SQLite 持久化
- 微信提醒

当前原型基于 2026 年 4 月的闲鱼主站 `https://www.goofish.com/`，搜索入口使用 `https://www.goofish.com/search?q=关键词`。抓取逻辑是 DOM 和接口返回的启发式提取，页面结构变更时需要调整 [scraper.py](/home/hao/work/misc/xianyu-sniper/src/xianyu_sniper/scraper.py)。

## 目录

```text
xianyu-sniper/
├── config.example.yaml
├── data/
├── requirements.txt
├── run.py
└── src/xianyu_sniper/
    ├── cli.py
    ├── config.py
    ├── db.py
    ├── notifier.py
    ├── scoring.py
    └── scraper.py
```

## 快速开始

1. 安装依赖

```bash
cd /home/hao/work/misc/xianyu-sniper
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

2. 修改配置

项目里已经放了可直接编辑的 [config.yaml](/home/hao/work/misc/xianyu-sniper/config.yaml)，也保留了一份 [config.example.yaml](/home/hao/work/misc/xianyu-sniper/config.example.yaml) 作为模板。

3. 配置微信提醒

支持三种模式：

- `wechat_work`：企业微信机器人 Webhook
- `serverchan`：Server 酱 `SendKey`
- `pushplus`：PushPlus `token`

如果你只是先验证抓取和评分，可以保留 `provider: none`。

4. 首次登录闲鱼

```bash
python run.py login --config config.yaml
```

脚本会打开 Chromium 持久化浏览器。你在浏览器里完成扫码登录后，回到终端按回车即可。登录态会保存在 `data/browser-profile/`。

5. 先跑一次

```bash
python run.py run-once --config config.yaml
```

6. 持续监控

```bash
python run.py watch --config config.yaml
```

## 运行方式

### 初始化数据库

```bash
python run.py init-db --config config.yaml
```

### 打印命中的高分商品

```bash
python run.py run-once --config config.yaml --print-matches
```

### 只打开浏览器验证登录态

```bash
python run.py login --config config.yaml --headful
```

## 配置说明

[config.example.yaml](/home/hao/work/misc/xianyu-sniper/config.example.yaml) 里已经给了默认值。重点字段：

- `keywords`：监控的商品关键词列表
- `keywords[].required_keywords`：必须包含的词
- `keywords[].excluded_keywords`：出现就跳过
- `keywords[].min_price / max_price`：价格带过滤
- `keywords[].min_score`：该关键词的提醒阈值
- `notifications.provider`：微信提醒方式
- `scoring.alert_threshold`：全局提醒阈值

## 分数模型

分数范围大致在 `0.0 ~ 1.0`：

```text
final_score =
    0.55 * deal_ratio
  + 0.15 * freshness_score
  + 0.15 * seller_score
  + 0.15 * info_quality_score
  - risk_penalty
```

其中：

- `deal_ratio`：估算市场价与当前价格的差值比例
- `freshness_score`：越新越高
- `seller_score`：卖家信用字段越完整越高
- `info_quality_score`：图片、标题、描述越完整越高
- `risk_penalty`：价格过低、命中高风险词、信息过少都会扣分

## 数据库

SQLite 默认保存在 `data/xianyu.sqlite3`。主要表：

- `listings`：抓到的商品、评分、通知状态

可以直接用 `sqlite3 data/xianyu.sqlite3` 查看。

## 已知限制

- 闲鱼页面和接口经常变，抓取逻辑不是长期稳定 API。
- 搜索结果里会混入广告、置顶和非标准卡片，代码已经做了去重和过滤，但不是 100% 干净。
- 当前估值是规则法，不是机器学习模型。
- 真正要提高命中率，最好把关键词收窄到型号级别，比如 `iPhone 15 256G 国行`，不要只配 `iPhone`。

## 建议的实战用法

- 每个关键词尽量聚焦到具体型号和容量。
- 先跑几天积累历史价格，估值会明显更稳。
- 初期把 `notifications.dry_run` 设为 `true`，先观察分数和误报。
- 如果你发现页面抓不到商品，先打开 [scraper.py](/home/hao/work/misc/xianyu-sniper/src/xianyu_sniper/scraper.py) 里的 DOM 选择器候选和接口提取逻辑。
