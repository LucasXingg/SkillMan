# SkillMan

SkillMan 是一个 CLI skill 管理器，通过本地命令收集现有 skills，再用软链接分发给 Codex、Claude 和其他 agent。

支持 macOS / Linux、Python 3.11+、zsh。只使用 Python 标准库，无全局安装，无 npx，无运行时下载或网络访问。启动器会从 PATH 中选择兼容的 Python；macOS 自带 Python 可能版本过低，需要自行准备 Python 3.11+。

## 开始使用

在本仓库执行：

```sh
./bin/skillman init
source "$PWD/scripts/hook.zsh"
skillman open
```

希望新终端自动加载时，将下面这行加入 `~/.zshrc`，换成实际仓库路径：

```sh
source "/absolute/path/to/SkillMan/scripts/hook.zsh"
```

进入仓库或其子目录时，hook 将本地 `bin` 加入 PATH；离开后移除。hook 不会移动或分发文件。无需 hook 时，也可以直接运行 `/absolute/path/to/SkillMan/bin/skillman`。

`open` 使用 VS Code 的 `code` 命令；未找到时打印文件夹路径，可从 VS Code 手动打开。

## 收集、编辑、分发

先检查 `.skillman/config.toml` 中的来源和目标，再执行：

```sh
skillman collect --dry-run
skillman collect
# 在 VS Code 中编辑 skills/<名称>/ 下的任意文件
skillman deploy --dry-run
skillman deploy
skillman status
```

**collect 会移动原目录，直到 deploy 执行后才建立回链。** 请在 agent 不使用这些文件时执行。默认分发到全部启用目标；修改集中目录后，已有软链接会直接读取最新内容，无需再次复制。

额外收集项目目录，以及只分发到指定 agent：

```sh
skillman collect --from /path/to/project/.agents/skills --dry-run
skillman collect --from /path/to/project/.agents/skills
skillman deploy --target codex
skillman deploy --target codex --target claude
```

`--from` 和 `--target` 可重复指定。额外来源与配置来源一起扫描。指定目标时允许选择配置中 `enabled = false` 的目标，但批量分发仍遵循每个 skill 的 `.skillman` 开关。原位置属于配置目标时也遵循目标筛选；其他额外来源仅在未指定 `--target` 时回链。

每个 skill 顶层的 `.skillman` 是 TOML 格式的分发开关文件，用于控制该 skill 分发到哪些 agent，与管理目录中的 `.skillman/` 配置和状态目录不同。例如：

```toml
codex = true
claude = false
```

`collect` 会为没有此文件的 skill 创建 `.skillman`，将当前配置的所有目标设为 `true`；已有文件保持不变。批量 `deploy`（包括仅指定 `--target`）只向开关为 `true` 的目标建立链接，同时仍遵循目标级 `skills` 子集。文件存在但未列出的目标默认关闭；没有此文件的旧 skill 或手工添加的 skill 则默认允许全部目标。

单独执行 `skillman deploy review --target codex` 时，会将该 skill 的 `codex` 开关保存为 `true`，并覆盖目标级 `skills` 子集限制；`--dry-run` 只预览，不写入。将开关改为 `false` 后，需执行 `deploy` 才会清理本次同步范围内由 SkillMan 创建且未被外部改动的旧链接，不会删除 skill 内容或覆盖实体目录。`.skillman` 不参与内容去重，合并相同内容时保留集中目录已有的开关配置。

## 配置

路径支持 `~`、绝对路径和相对管理目录根的路径。以文件夹内的 `SKILL.md` 识别 skill；找到后将整个文件夹作为一个单元，不继续拆分内部目录。隐藏文件和内部软链接会保留，扫描不跟随目录软链接。

```toml
sources = ["~/.codex/skills", "~/.claude/skills", "~/.agents/skills"]
exclude = [".system", ".git", "node_modules", "cache", ".cache", "plugins"]

[selection]

[targets.codex]
path = "~/.codex/skills"
enabled = true
skills = ["review", "writing"] # 省略即分发所有无冲突的 skills

[targets.custom]
path = "/path/to/project/.agent/skills"
enabled = false
```

排除项按目录名称匹配。不存在的来源跳过；读取权限不足会报告错误。默认跳过 agent 系统 skills 和插件缓存，不会扫描整块磁盘。

## 重名与冲突

同名且内容、文件权限及内部链接一致的 skills 合并，保留所有原路径记录。内容不同的版本保存为 `名称--内容摘要`，分发时该组报告 `CONFLICT`，其他无冲突项仍可分发，命令返回非零退出码。

选择版本时编辑配置：

```toml
[selection]
review = "review--0123456789"
```

选择的版本以 `review` 分发，并回链到该组全部原位置。也可以在 VS Code 中重命名版本目录来独立分发。若重命名了已收集目录，用 `[selection]` 中的 `原名称 = "新目录名"` 恢复原位置回链。版本分组记录保存在状态文件中，文件名本身可以包含 `--`。

手工新建 `skills/<名称>/SKILL.md` 即可添加 skill。删除或重命名目录后，`status` 会报告缺失源、待分发项或失效链接。

## 文件保护与恢复

- 目标已有实体目录或其他工具创建的链接时报告 `BLOCKED`，不覆盖。
- 重复分发保持幂等；仅替换状态记录中匹配的 SkillMan 链接。
- 移动管理目录后，更新 `.zshrc` 中 hook 路径并重新加载，再执行 `deploy` 修复原链接。相对配置路径也会随根目录变化。
- 收集采用复制、内容校验、记录状态、移除原目录的顺序，兼容跨文件系统。复制或校验失败保留源；不要在收集过程中并行编辑源文件。
- 状态以原子写入保存，操作日志辅助中断恢复；保留 `.skillman/state.json`，否则无法识别自己创建的旧链接。恢复不会主动删除残留源目录，可重跑 `collect` 校验并完成处理。
- 文件系统操作不是跨目录事务。删除源目录遇到权限或磁盘故障时，完整集中副本会保留，命令报错；检查两边内容后再处理残留。
- CLI 使用配置文件上的非阻塞文件锁；执行期间不要替换配置文件。
- `--dry-run` 和 `status` 不创建目录、移动文件、写入状态或修改链接。

运行时数据在 `.skillman/state.json` 中，不应手工编辑或提交。备份管理目录时保留该文件。首版不提供远程市场、编辑器扩展或 agent 会话管理。

## 测试

```sh
PYTHONPATH=src python3.11 -m unittest discover -s tests -v
```

测试只操作临时 skills 和目标，不触碰用户真实 agent 目录。覆盖收集、合并、冲突选择、软链接、移动失败、管理目录迁移、只读预览、互斥锁和实际 zsh hook 行为。

## 每个 skill 的分发开关与单独分发

`collect` 在收集到的目录顶层创建 `.skillman`（TOML），列出当前配置的所有目标，初始值为 `true`：

```toml
codex = true
claude = false
agents = true
```

已有 `.skillman` 文件会保留。批量 `deploy` 和 `deploy --target codex` 都读取此文件；只有相应目标为 `true` 才建立链接。文件存在但未列出的新目标默认关闭，需手动添加。旧版收集或手工添加且没有此文件的 skill 保持全部目标允许的兼容行为。目标级 `skills` 子集也仍然生效。

```sh
skillman deploy review                     # 只分发 review，遵循开关
skillman deploy review --target codex      # 只向 codex 分发 review，覆盖开关
skillman deploy review --target codex --dry-run
```

位置参数使用 `skills/` 下的实际目录名，可直接指定冲突组中的某一版本。单独指定 skill 且带 `--target` 时，先将 `.skillman` 中对应目标保存为 `true`（其他开关保留），再向显式目标分发；仍覆盖目标级 `skills` 子集，已有文件保护仍然生效。`--dry-run` 仅预览，不写配置。不带 `--target` 时仍遵循开关和启用目标。

开关设为 `false` 后执行 `deploy`，会删除该范围内之前由 SkillMan 创建且未被外部改动的链接，使实际状态与配置同步。无参数时同步全部记录（包括已禁用或移除的目标）；`--target` 只同步指定目标，位置参数只同步指定 skill，其他目标和 skills 保持不变。冲突组暂不清理，实体目录和已被外部修改的链接会保留并报告 `BLOCKED`。`.skillman` 是管理元数据，不参与 skill 内容去重校验；合并相同内容时保留集中目录已有的开关配置。
