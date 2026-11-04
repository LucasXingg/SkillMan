# SkillMan

将分散在各个 agent 目录里的 skills 集中到一处编辑，再通过软链接分发给 Codex、Claude 和其他 agent。

SkillMan 是一个离线 CLI 工具，只使用 Python 标准库，无需全局安装或下载依赖。集中目录里的修改会通过已有软链接立即生效，省去多处复制维护。

## 快速开始

需要 **macOS / Linux 和 Python 3.11+**；目录命令 hook 需要 **zsh**。启动器会从 PATH 中选择兼容的 Python，macOS 自带版本可能过低。

在本仓库根目录执行：

```sh
./bin/skillman init
source "$PWD/scripts/hook.zsh"
skillman --help
```

`init` 创建缺失的配置、状态文件和 `skills/` 目录，不覆盖已有配置。随后按以下流程收集和分发：

1. 检查 `.skillman/config.toml` 中的来源与目标。默认配置使用 `~/.codex/skills`、`~/.claude/skills` 和 `~/.agents/skills`；已有配置以文件内容为准。
2. 预览收集计划，确认后执行：

   ```sh
   skillman collect --dry-run
   skillman collect
   ```

3. 编辑 `skills/` 中的内容，预览并执行分发：

   ```sh
   skillman deploy --dry-run
   skillman deploy
   skillman status
   ```

**`collect` 会移走来源中的 skill 目录，`deploy` 才会建立回链。** 请在 agent 不使用这些文件时执行，并在收集后完成分发。正常分发会输出 `LINK`，已有正确链接输出 `OK`；`status` 会显示 skills、异常项及分发预览。

## 常用操作

以下命令在已加载 hook 的仓库目录内执行；也可以用 `./bin/skillman` 替代 `skillman`。

| 命令 | 用途 |
| --- | --- |
| `skillman init` | 创建缺失的管理文件 |
| `skillman new` | 交互创建 skill 模板 |
| `skillman open` | 在 VS Code 中打开管理目录；未找到 `code` 时打印目录路径 |
| `skillman collect` | 从配置来源收集 skills |
| `skillman deploy` | 同步分发链接，并清理本次范围内不再需要的受管链接 |
| `skillman status` | 查看 skills、冲突和链接状态，只读 |

### 新建 skill

```sh
skillman new
# 预先提供名称，交互填写描述和可选执行说明
skillman new code-review
```

也可通过参数创建，适合脚本使用：

```sh
skillman new code-review \
  --description "审查代码变更，在代码评审时使用" \
  --instructions "阅读变更，检查正确性并给出建议" \
  --no-interactive
```

生成 `skills/code-review/SKILL.md` 和该 skill 的 `.skillman` 分发开关文件。编辑模板后执行 `skillman deploy code-review` 即可分发。也支持手工创建 `skills/<名称>/SKILL.md`。

名称最长 64 个字符，由小写字母、数字及单个连字符连接。已有同名目录或链接时拒绝创建；Ctrl-C 或输入结束会取消。`--no-interactive` 必须提供名称和非空描述，省略执行说明时保留 TODO；`--dry-run` 只预览，不写文件。

### 额外来源与指定目标

```sh
# 额外来源与配置来源一起扫描；将示例路径替换为实际路径
skillman collect --from /path/to/project/.agents/skills --dry-run
skillman collect --from /path/to/project/.agents/skills

# 批量分发到指定目标，仍遵循每个 skill 的开关
skillman deploy --target codex
skillman deploy --target codex --target claude

# 只分发一个 skill
skillman deploy code-review
skillman deploy code-review --target codex --dry-run
skillman deploy code-review --target codex
```

`--from` 和 `--target` 均可重复指定。skill 位置参数使用 `skills/` 下的实际目录名，也可指定冲突组中的某个版本。

**单独指定 skill 且带 `--target` 时，会把对应分发开关保存为 `true`，并覆盖目标级 `skills` 子集限制。** 其他开关保留，已有文件保护仍生效；`--dry-run` 不保存开关。

### zsh 目录命令

希望新终端自动加载时，将下面一行加入 `~/.zshrc`，替换为实际仓库路径：

```sh
source "/absolute/path/to/SkillMan/scripts/hook.zsh"
```

进入仓库或其子目录时，hook 将本地 `bin` 加入 PATH；离开后撤销该项。hook 本身不收集或分发文件。不使用 hook 时，可直接运行 `/absolute/path/to/SkillMan/bin/skillman`，启动器始终使用其所在仓库作为管理目录。

## 配置与分发规则

管理配置位于 `.skillman/config.toml`。路径支持 `~`、绝对路径和相对管理目录根的路径。以下示例可按需调整：

```toml
sources = ["~/.codex/skills", "~/.claude/skills", "~/.agents/skills"]
exclude = [".system", ".git", "node_modules", "cache", ".cache", "plugins"]

[selection]

[targets.codex]
path = "~/.codex/skills"
enabled = true
disable_default_backlink = false
# skills = ["code-review", "writing"]

[targets.claude]
path = "~/.claude/skills"
enabled = true
disable_default_backlink = false

[targets.agents]
path = "~/.agents/skills"
enabled = true
disable_default_backlink = false
```

| 配置项 | 含义 |
| --- | --- |
| `sources` | 收集来源；不存在的目录跳过，读取权限不足会报错 |
| `exclude` | 按目录名称排除，默认跳过系统 skills、插件目录和常见缓存目录 |
| `targets.<名称>.path` | 分发目标目录，可添加自定义目标 |
| `enabled` | 是否参与未指定 `--target` 的分发，省略时为 `true` |
| `disable_default_backlink` | 是否将新生成的 skill 开关初始化为 `false`，省略时为 `false` |
| `skills` | 该目标允许的 skill 名称子集；省略时不限制，冲突组使用原名称 |
| `selection` | 为重名冲突组选择实际分发的目录，见下文 |

扫描以目录内的 `SKILL.md` 识别 skill；找到后将整个目录作为一个单元，不拆分内部目录。隐藏文件和内部软链接会保留，扫描不跟随目录软链接，也不会扫描整个磁盘。

### 每个 skill 的分发开关

`skills/<名称>/.skillman` 是 TOML 文件，与管理配置所在的根目录 `.skillman/` 不同：

```toml
codex = true
claude = false
agents = true
```

`new` 和 `collect` 会在缺失时生成此文件，列出所有配置目标。各目标的 `disable_default_backlink = true` 会让对应开关初始化为 `false`，否则为 `true`。它只影响之后生成的开关文件，已有文件保持不变，建立链接仍需执行 `deploy`。

分发时按以下规则选择目标：

| 调用方式 | 目标选择 | skill 开关与目标 `skills` 子集 |
| --- | --- | --- |
| `deploy` 或 `deploy code-review` | 所有启用目标 | 遵循 |
| `deploy --target codex` | 指定目标，即使 `enabled = false` | 遵循 |
| `deploy code-review --target codex` | 指定目标，即使 `enabled = false` | 保存开关为 `true`，覆盖子集限制 |

开关文件存在但未列出的目标默认关闭；旧 skill 或手工添加的 skill 没有开关文件时，默认允许全部配置目标。`.skillman` 不参与内容去重，合并相同内容时保留集中目录已有的开关。

### 回链与清理范围

原位置直接位于配置目标目录下时，回链遵循相同的目标筛选与开关规则。其他额外来源的原位置仅在未指定 `--target` 时回链。

将开关改为 `false` 后执行 `deploy`，会清理本次同步范围内不再需要、且仍匹配状态记录的 SkillMan 链接：

- 无参数：同步全部记录，包括已禁用或移除的目标、已删除的 skills。
- `--target`：只同步指定目标。
- skill 位置参数：只同步该 skill；两者同时指定时进一步限定目标。

范围外的部署保持不变，冲突组暂不清理。实体目录和被外部修改的链接会保留并报告 `BLOCKED`。删除或重命名集中目录后，可先运行 `status` 检查缺失源、待分发项或失效链接。

## 重名与冲突

同名且内容、权限和内部链接一致的 skills 会合并，保留全部原路径记录。内容不同的版本保存为 `名称--内容摘要`。

批量分发时，未选择版本的组会报告 `CONFLICT`；其他无冲突项仍会分发，命令返回非零退出码。在配置中选择实际存在的版本目录即可解决，例如：

```toml
[selection]
review = "review--0123456789" # 替换为 skills/ 下实际的目录名
```

所选版本会以 `review` 分发，并按回链规则恢复该组的原位置链接。版本分组保存在状态文件中，名称本身也可以包含 `--`。

也可以重命名版本目录来独立分发。若重命名了已收集目录，需要在 `[selection]` 中设置 `原名称 = "新目录名"`，恢复原位置回链。

## 文件保护与恢复

- **已有文件保护**：目标已有实体目录或外来链接时报告 `BLOCKED`，不覆盖。重复分发保持幂等；仅替换或移除仍匹配状态记录的受管链接。
- **收集保护**：先复制并校验内容，再记录状态、移除源目录，支持跨文件系统。复制或校验失败会保留源；收集期间不要并行编辑源文件。
- **中断恢复**：状态原子写入，操作日志辅助恢复。恢复不会主动删除残留源目录，可重跑 `collect` 校验并完成处理。
- **故障处理**：操作不是跨目录事务。删除源目录遇到权限或磁盘故障时，完整集中副本会保留，命令报错；检查两边内容后再处理残留。
- **并发保护**：CLI 使用配置文件上的非阻塞文件锁；执行期间不要替换配置文件。
- **只读检查**：`--dry-run` 和 `status` 不创建目录、移动文件、写状态或修改链接。

运行时状态保存在 `.skillman/state.json`，请勿手工编辑或提交。**备份和迁移管理目录时保留此文件**，否则无法识别 SkillMan 创建的旧链接。迁移后更新 `.zshrc` 中的 hook 路径并重新加载，再执行 `deploy` 修复原链接；相对配置路径会随根目录变化。

## 开发与测试

核心逻辑位于 [src/skillman/cli.py](src/skillman/cli.py)，启动器和目录 hook 分别位于 [bin/skillman](bin/skillman) 与 [scripts/hook.zsh](scripts/hook.zsh)。无需构建或第三方依赖。

在仓库根目录运行完整测试（`python3` 需为 3.11+）：

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

测试只操作临时 skills 和目标，不触碰真实 agent 目录，覆盖收集、去重、冲突选择、分发开关、范围清理、故障恢复、迁移、只读预览、互斥锁和 zsh hook。未安装 zsh 时跳过 hook 测试。修改行为时请补充回归测试，仓库约定见 [AGENTS.md](AGENTS.md)。

## 许可

仓库尚未提供项目级 `LICENSE` 文件；各 skill 的许可请查看其自身声明。
