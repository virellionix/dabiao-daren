# `main` 分支保护

仓库的 `main` 采用“必须经过 Pull Request + CI 通过”的保护策略：

- 禁止直接 push、强制推送和删除分支；
- 必须通过 `test` 状态检查；
- PR 至少 0 个批准也必须走 PR（适合个人仓库保留变更记录）；
- 管理员也受规则约束；
- 允许普通合并，不强制线性历史。

如果当前 GitHub 账户计划不允许私有个人仓库启用保护，先把仓库公开或升级计划，再在仓库 Settings → Branches → Add branch ruleset/branch protection 中应用同样配置。GitHub 对私有仓库的可用性由账户计划决定，不能靠仓库文件绕过。

本地检查：

```bash
gh api repos/virellionix/dabiao-daren/branches/main/protection
```

保护规则是 GitHub 远端设置，不写入 Skill 本身；仓库源码里的 CI 只是所要求的 `test` 检查。
