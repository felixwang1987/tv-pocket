# 影视口袋

和「发票口袋」一样，是可直接打开、放到 GitHub Pages 的自用 HTML 小工具。

支持：搜索、多仓/线路合集/单配置/直播筛选、一键复制、当前浏览器收藏、JSON 清单导出。只读取配置与播放列表，不执行来源中的插件代码。

## 先打开看看

双击 **index.html**。页面已内嵌一份真实采集快照，不依赖服务器或安装软件。

直播列表会抽检视频画面；多仓、线路合集和单配置标记为「配置可读 · 需实播」。抽检出画面不代表整份列表都能播放，也不保证不同地区或运营商网络可以访问。

## 推荐：发布后每 6 小时自动找新链接

1. 在 GitHub 创建一个 **Public** 仓库，例如 `tv-pocket`，默认分支用 `main`。
2. 解压完整发布包，把**文件夹里面的内容**放到仓库根目录，确保根目录直接能看到 `index.html`、`scripts`、`data`、`tests`、`sources.config.json`。
3. 确认仓库还有 **`.github/workflows/update.yml`**。它是定时更新的关键文件。Mac Finder 按 **Command + Shift + .** 显示隐藏文件夹再上传。也可以在 GitHub 的 **Add file → Create new file** 中输入这个完整路径，粘贴本包同名文件的内容。
4. 打开仓库 **Settings → Pages**，在 **Build and deployment → Source** 选择 **GitHub Actions**。
5. 打开 **Actions → 更新影视口袋 → Run workflow → Run workflow**。等 `collect` 和 `deploy` 都变成绿色。
6. 回到 **Settings → Pages** 打开网站地址。之后大约每 6 小时自动更新；GitHub 定时任务可能排队延迟。

普通项目地址形如 `https://你的用户名.github.io/tv-pocket/`。这个自用网页的访问地址是公开的，收藏仅保存在本机浏览器。

工作流使用 GitHub 自动提供的 `GITHUB_TOKEN`，无需把密码或令牌填入 HTML。

**重要：不要把 ZIP 压缩包本身直接上传当网站，也不要把所有文件多套一层目录。**

## 如果只想上传一个 HTML

把 `index.html` 上传到仓库根目录，然后在 **Settings → Pages** 选择 **Deploy from a branch → main → / (root) → Save**。这样可以使用搜索、筛选、复制、收藏和导出，但只是该文件附带的快照，不会在后台发现新链接。之后可以再补齐自动更新文件。

## 日常使用

- **复制已实测直播**：首页按钮会复制 `checked/live.m3u` 的线上地址，直接放入影视仓「直播配置」。只收录近 18 小时成功解码画面的抽检频道，按地址去重。未出画面的频道不会进入这份清单。
- **检测明细**：展开卡片可看每个抽检频道的结果、原因和检测时间。多仓显示下级配置的可读情况，不假定点播可用。
- **复制地址**：到影视仓对应的多仓、配置或直播入口粘贴；入口名称依软件版本不同。
- **收藏**：点卡片右上角星星，底部「我的收藏」集中显示。同一浏览器刷新仍保留，不跨设备同步。
- **读取最新**：读取已经发布的检查结果。它不会触发 GitHub 采集；想立即采集，到 Actions 手动运行。
- **导出当前列表**：下载当前搜索/筛选结果的 JSON 清单，便于备份和检查；不是通用播放器配置文件。
- 超过 **18 小时**没有检查的数据显示「待重新检查」。请求失败的旧记录仍保留，并显示失败状态。

## 自动采集做了什么

从 GitHub 仓库搜索发现相关公开项目，读取 README 和候选配置，再解析一层配置子链接。结果按 URL 去重并保留发现来源。每轮最多扫描 14 个仓库、检查 120 个候选，每个文件限 3 MB、单次请求超时 12 秒；不是穷尽搜索整个 GitHub。

识别：`storeHouse` 多仓、`urls` 线路合集、`sites` 单配置、M3U 播放列表、HLS 流、直播 TXT。JSON 注释和尾逗号可以解析，但不保证所有播放器都支持非标准 JSON。带验证信息、加密配置、插件脚本或未知结构会被略过。

初始快照来自 2026-09-23（America/Los_Angeles）的真实运行：17 条，扫描 14 个仓库、检查 113 个候选。由于本机代理 DNS，初次仅检查四个固定 GitHub 域名；其中一个仓库发现过程连接失败，页面已有提示。上传后 Actions 使用正常公共地址校验，会继续尝试第三方域名。数量与可访问性会随上游变化。

## 常见问题

**Actions 的保存快照步骤提示权限不足**：检查 Settings → Actions → General → Workflow permissions 是否允许仓库工作流写入。组织策略或分支保护也可能禁止机器人提交；此时需要仓库管理员允许该工作流写入，或使用自己的独立仓库。

**部署失败**：先确认 Pages 的 Source 是 GitHub Actions，再重新运行。不要同时启用分支部署和本工作流部署。

**长时间不更新**：查看 Actions 最近一次运行。公共仓库连续 60 天没有活动时 GitHub 可能停用定时任务，需要在 Actions 重新启用。本工作流每轮保存检查快照，但仍应以页面时间和 Actions 状态为准。

**全部来源失败**：不会删除旧清单；检查网络、GitHub API 限流和 Actions 日志后重跑。配置检查结果与播放结果分别判断。

**要多收录几个仓库**：编辑 `sources.config.json` 的 `repositories`；想固定一个链接，在 `seeds` 里按现有格式添加名称、URL 和 GitHub 来源。无需修改 HTML。

## 本地维护（可选）

需要 Python 3.9 或更高版本，以及 FFmpeg。Python 无第三方依赖；GitHub Actions 会检查并安装 FFmpeg：

```sh
python3 -m unittest discover -s tests -v
python3 scripts/collector.py
python3 scripts/build.py --site
```

本机若使用代理的 Fake-IP DNS，可加 `--github-only`，此模式只允许四个固定 GitHub HTTPS 域名，不检查第三方域名，也不运行实播抽检。Actions 不需要这个参数。普通模式拒绝私有/本地地址，并在重定向时重新检查目标；请在 GitHub 托管 runner 或隔离环境运行第三方来源采集。

`data/sources.json` 保存结构化数据；`index.html` 内嵌同一份数据；`checked/live.m3u` 是实播抽检通过的频道列表；`_site/` 是生成的发布目录。工作流只发布网页与清单，不发布脚本或设计文档。

## 验证范围

交付前验证解析、去重、失败保留、私有地址拒绝、HTML 安全内嵌，并检查页面主要操作与响应布局。GitHub Actions 的真实定时和 Pages 部署需要在上传启用后验证。每轮最多抽检 48 份直播列表、每份 3 个频道，样本每 6 小时轮换。HTTP 成功还必须下载视频数据并解码出一帧才计入通过；总抽检时限约 6 分钟、最多 500 次请求。加密、特殊请求头、分段字节范围、非 HTTP(S) 和非 80/443 端口目前归为需客户端验证。多仓/线路合集最多抽查 24 份、每份 3 个下级文件，单配置不执行 JAR/JS，不验证网盘登录或会员权限。

官方参考：[GitHub Pages 自定义工作流](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)、[GitHub 定时任务](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)、[GitHub 仓库搜索 API](https://docs.github.com/en/rest/search/search#search-repositories)。
