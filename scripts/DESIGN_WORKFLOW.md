# 设计变更流程

## 原则

- `scripts/template.html` 是设计的**唯一真相来源（SSOT）**
- `docs/index.html` 是**自动生成物**，不要直接编辑
- 任何设计变更都必须通过修改 `template.html` → commit → push → workflow 自动生效

## 设计变更流程

### 1. 修改设计

```bash
# 打开 scripts/template.html 进行修改
```

### 2. 本地预览

```bash
cd "D:\共享文件\AI协作工作区\01_工作文件区\weekly-report-repo"

# 生成本地预览文件（不覆盖 index.html）
py -3 scripts/generate_html.py --preview

# 启动本地服务器（另一个终端）
python -m http.server 8000 --directory docs

# 浏览器访问 http://localhost:8000/preview.html
```

### 3. 验证效果

- [ ] 所有功能正常
- [ ] 数据正确渲染
- [ ] 响应式布局正常

### 4. 提交发布

```bash
git add scripts/template.html
git commit -m "feat: [描述设计变更]"
git push
```

GitHub Actions 会自动跑三个 job（`update.yml`）：

1. **`collect`**（30 分钟上限）：先跑全部离线测试做门控（任一失败即阻断，不进入采集），再采集 + AI 分析，然后**立刻把 `data/` 提交落库**——这是数据检查点，不等渲染。渲染超时也不会吞掉当天数据。
2. **`render`**（90 分钟上限，`needs: collect`）：先同步到最新 main，再生成 `docs/index.html` + `docs/feed.xml`，最后提交 `docs/` 与 `data/`。
3. **`health-check`**（`needs: render`）：同步最新 main 后跑 `check_data_health.py --quick --strict`，失败开告警 Issue。

注意两点：

- `html-only` **不再由 workflow 自动判断**，只能手动触发（Actions 页面 → Run workflow → mode 选 `html-only`），该模式跳过采集与健康检查。
- 推送要**避开采集时段**（北京 02:00 / 09:00）。`concurrency` 是 `cancel-in-progress: true`，撞上会取消正在跑的运行。`data/` 与 `docs/` 的自动提交不在 `on.push.paths` 里，不会自我取消。

## 紧急回滚

如果生产环境出现问题：

```bash
# 1. 从 Git 历史恢复 template.html
git log --oneline scripts/template.html
git checkout <commit-hash> -- scripts/template.html
git push

# 2. 或手动触发 workflow 重新生成
# 访问 GitHub Actions 页面 → Update workflow → Run workflow → html-only 模式
```

## 设计备份

重大改版前创建 Git tag：

```bash
git tag -a v1.0-hero-carousel -m "Hero轮播设计"
git push origin v1.0-hero-carousel
```

## 命令行参数

| 参数 | 作用 |
|------|------|
| `python scripts/generate_html.py` | 默认：生成到 index.html（有内容对比优化） |
| `python scripts/generate_html.py --force` | 强制重写 index.html（workflow 使用） |
| `python scripts/generate_html.py --preview` | 生成本地 preview.html（不覆盖 index.html） |
