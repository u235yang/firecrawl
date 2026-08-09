# 项目文档

本目录集中存放京东云多用户 Firecrawl 项目的设计、部署、运维、安全、验收和决策文档。Firecrawl 上游通用说明仍保留在仓库根目录。

当前不在 `docs/` 内预建分类子目录。新增文档先直接放在本目录，并通过本文件维护索引；只有当文档数量和维护边界确实需要时，再单独确认是否分层。

## 相关入口

- [Cloudflare 公网注册与用户 API Key 技术方案](cloudflare-registration-api-key-design.md)
- [京东云 Firecrawl 鉴权服务：现状与解决方案](jingdong-firecrawl-auth-service.md)
- [项目目标与 Agent 约束](../AGENTS.md)
- [Firecrawl 上游项目说明](../README.md)
- [Firecrawl 上游自托管说明](../SELF_HOST.md)
- [上游贡献指南](../CONTRIBUTING.md)

## 维护规则

- 区分“当前已验证状态”“设计方案”和“目标能力”，不要把目标写成已完成事实。
- 运行状态、IP、端口、资源规格和软件版本属于动态事实，记录检查时间并在使用前重新验证。
- 文档中不得保存真实 API Key、密码、Token、私钥或 `.env` 内容。
- 重大技术选择应留下 ADR，说明背景、选择、替代方案和影响。
- 运行手册必须包含前置检查、执行步骤、成功标准、失败处理和回滚边界。
