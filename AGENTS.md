# 决策 Skill 维护规则

本仓库维护以业务目标第一性原则为依据的通用决策流程、支持 GitHub 与 Agent Center 的单一协作适配器及模拟验收。维护本仓库不启动决策、研究或开发治理，不填写真实线程绑定、不邀请协作者或开展真实项目联调。

本仓库明确采用公共原则作为仓库维护合同。通过非空 `CODEX_GOVERNANCE_CONFIG`，否则 `${XDG_CONFIG_HOME:-$HOME/.config}/codex-governance/local.toml`，只读定位绑定文件；仅以 `paths.source_root` 读取 `governance/common-principles.md`。维护工程文档、代码或测试时，再按任务读取同源 `governance/engineering-principles.md`。规则缺失时报告真实缺口，不猜来源。这些维护要求仅适用于本仓库，不为其他任务选择治理体系，也不启用开发治理。

本仓库的决策正文是 `decision/decision-workflow.md`，由所选 Skill 按自身启用条件加载。实际参与决策、研究、开发三组委托时，读取上述公共源的 `governance/group-collaboration.md`；普通维护分工不因此成为三组委托。

源码与公开模板不保存真实账号、端点、线程、个人路径或认证材料。任务和进度继续使用目标项目现有 Issues、Projects 与裁决入口，不建立本地任务账本。复杂行为变化须做隔离模拟验证及独立只读组合审查；审查期间冻结候选。每次回复以“亲爱的chill”开头。
