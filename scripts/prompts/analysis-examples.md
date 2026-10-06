示例1（融资大额）：
标题: "Mistral raises $830M, 9fin hits unicorn status"
输出: {"url":"","event_types":"funding","content_overview":"法国AI公司Mistral完成8.3亿美元融资，金融科技公司9fin同期晋级独角兽","summary_short":"Mistral获$830M融资，9fin晋级独角兽","reason":"欧洲AI独角兽获顶级融资，后续可能开放生态合作和API采购","impact":"AI基础设施供应商、云服务商、API集成商","insight_label":"资金流向","trend_topic":"欧洲AI融资热潮","score":9,"canonical_company":"Mistral","canonical_key":"830m"}

示例2（财报方向）：
标题: "Nubank Q1 revenue up 34% to $2.8B"
输出: {"url":"","event_types":"earnings","content_overview":"巴西数字银行Nubank一季度营收28亿美元，同比增长34%","summary_short":"Nubank营收$2.8B，同比+34%","reason":"拉美数字银行持续高增长，东南亚复制模式具有参考价值","impact":"拉美金融科技合作方、银行科技供应商","insight_label":"背景补充","trend_topic":"拉美FinTech高增长","score":6,"canonical_company":"Nubank","canonical_key":"2.8b"}

示例3（"Report"是"据报道"而非研报）：
标题: "Cursor To Open First India Office By 2026 End: Report"
输出: {"url":"","event_types":"strategy","content_overview":"AI编程公司Cursor计划在2026年底前开设印度首个办公室","summary_short":"Cursor计划2026年底开印度办公室","reason":"AI编程工具公司加速全球化布局，亚太开发者市场战略地位上升","impact":"印度开发者生态、AI工具渠道合作方","insight_label":"合作机会","trend_topic":"AI编程工具全球化","score":5,"canonical_company":"Cursor","canonical_key":""}

示例4（财报亏损必须高分档，禁止给4-5）：
标题: "Zaggle plunges 20% to hit lower circuit after Q1 profit slump"
输出: {"url":"","event_types":"earnings","content_overview":"印度金融科技SaaS公司Zaggle一季度利润大幅下滑，股价暴跌20%触及单日跌停","summary_short":"Zaggle利润下滑股价暴跌20%","reason":"印度金融科技高估值股业绩失速引发估值修正，同类SaaS公司财报风险需关注","impact":"印度SaaS板块、金融科技投资者","insight_label":"警示信号","trend_topic":"印度金融科技估值修正","score":8,"canonical_company":"Zaggle","canonical_key":"20%"}

示例5（微小事件必须低分1-3）：
标题: "X adds video overlays"
输出: {"url":"","event_types":"strategy","content_overview":"社交平台X为视频功能增加叠加层小工具","summary_short":"X增加视频叠加功能","reason":"社交平台常规功能迭代，为视频创作者提供新工具","impact":"视频创作者、品牌营销方","insight_label":"背景补充","trend_topic":"社交产品功能迭代","score":3,"canonical_company":"X","canonical_key":""}
