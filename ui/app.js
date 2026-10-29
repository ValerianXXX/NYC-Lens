const COPY = {
  zh: { "assistant.title": '用自然语言比较两个地址',
  "assistant.hint": '将两个地址分别放在引号内。查询前确认正式餐饮业态和直线半径；可选 AI 默认关闭。',
  "assistant.request": '选址要求', "assistant.interpret": '准备比较',
  "assistant.notice": 'Demo Avenue 为合成演示地址，仅在演示模式可用。使用本地真实数据时请填写实际地址。',
  "assistant.first": '第一地址', "assistant.second": '第二地址', "assistant.choose": '请选择支持的业态',
  "assistant.scope": '直线半径', "assistant.confirm": '确认并比较',
    "accessibility.skip": "跳到分析工作台",
    "accessibility.primaryNav": "主要导航",
    "accessibility.export": "导出分析",
    "accessibility.comparisonExport": "导出比较结果",
    "accessibility.tabs": "分析内容",
    "accessibility.theme": "切换深色模式",
    "nav.single": "单点分析",
    "nav.compare": "多址比较",
    "nav.evidence": "数据说明",
    "header.local": "仅本机运行",
    "header.eyebrow": "本地 · 可追溯 · 大纽约",
    "header.title": "商业选址情报工作台",
    "form.address": "地址",
    "form.addressPlaceholder": "输入大纽约范围内的公开地址",
    "form.businessType": "商业类型",
    "form.focusArea": "当前查看商圈",
    "form.analyze": "开始分析",
    "business.fullService": "正式餐饮",
    "status.ready": "系统使用本地数据库；首次步行商圈会读取公开 OSM 网络。",
    "status.noCatchment": "请至少选择一个商圈。",
    "status.analyzing": "正在标准化地址、生成商圈并连接本地数据……",
    "status.success": "分析完成。缺失项 {count} 个；每个指标均保留来源、年份、类型和置信度。",
    "status.failure": "分析失败，请稍后重试。",
    "status.settingsChanged": "商圈设置已经改变，请重新分析以生成对应结果。",
    "status.invalidResponse": "服务器返回了无法读取的结果，请重新分析。",
    "status.httpFailure": "服务器暂时无法完成请求（HTTP {status}），请稍后重试。",
    "filters.summary": "筛选与商圈",
    "filters.eyebrow": "分析设置",
    "filters.title": "商圈范围",
    "filters.multi": "可多选",
    "filters.distance": "距离范围",
    "filters.radiusNote": "直线缓冲范围",
    "filters.walk": "步行范围",
    "filters.walkNote": "基于道路网络",
    "filters.methodTitle": "计算说明",
    "filters.methodBody": "距离范围使用适合纽约的投影坐标计算；步行范围根据本地保存的道路网络生成，不以圆形冒充步行时间。",
    "filters.reset": "恢复默认范围",
    "map.eyebrow": "空间视图",
    "map.title": "地址与商圈",
    "map.awaiting": "等待分析",
    "map.comparison": "比较 {count} 个地址 · {scope}",
    "map.synthetic": "合成演示",
    "map.aria": "纽约地图与商圈范围",
    "map.attribution": "底图 © OpenStreetMap contributors；商圈几何与结果仅保存在本机。",
    "tabs.overview": "概览",
    "tabs.audience": "客群",
    "tabs.competition": "竞争",
    "tabs.mobility": "交通",
    "tabs.property": "地产与风险",
    "tabs.evidence": "数据证据",
    "details.title": "指标与证据",
    "details.empty": "完成一次分析后显示标准地址、地块、商圈和数据版本。",
    "details.noData": "分析后显示当前商圈数据。",
    "details.audience": "客群判断",
    "details.visitorHealth": "游客、健康与环境",
    "details.demographics": "居民、收入与人口结构",
    "details.competition": "竞争、替代与需求补充",
    "details.mobility": "交通与工作人口",
    "details.property": "房地产、开发与监管",
    "details.sources": "来源、年份与置信度",
    "decision.eyebrow": "决策阅读",
    "decision.title": "选址判断摘要",
    "decision.noScore": "不生成综合分数",
    "decision.emptyTitle": "等待地点分析",
    "decision.emptyBody": "完成分析后，这里会用真实指标归纳地点概览、主要机会、主要风险和证据完整度。",
    "decision.location": "地点概览",
    "decision.opportunities": "主要机会",
    "decision.risks": "主要风险",
    "decision.evidence": "证据充分度",
    "decision.address": "标准地址",
    "decision.area": "所属区域",
    "decision.parcel": "地块",
    "decision.scope": "当前商圈",
    "decision.unmatched": "未匹配",
    "decision.marketProfile": "机会画像：{value}",
    "decision.audienceSignal": "客群判断为“{value}”，依据透明代理指标分类。",
    "decision.populationHigh": "常住人口处于纽约同口径第 {percentile} 百分位。",
    "decision.jobsHigh": "白天岗位处于纽约同口径第 {percentile} 百分位。",
    "decision.transit": "当前商圈覆盖 {subway} 个地铁站复合体和 {bus} 个公交停靠点。",
    "decision.complements": "商圈内识别到 {count} 个需求补充设施。",
    "decision.opportunityFallback": "当前快照未形成强机会信号；请结合详细指标和实地调查判断。",
    "decision.competitionSignal": "竞争压力：{value}。",
    "decision.regulatorySignal": "监管记录筛查：{value}。",
    "decision.environmentSignal": "环境覆盖筛查：{value}。",
    "decision.missing": "当前结果仍有 {count} 个缺失项。",
    "decision.riskFallback": "当前公开数据未显示突出风险信号；仍需核验租约、现场条件和最新监管记录。",
    "decision.traceability": "来源与年份覆盖",
    "decision.confidence": "指标置信度覆盖",
    "decision.sources": "独立数据源",
    "decision.sourceCount": "{count} 个",
    "decision.evidenceNote": "充分度描述数据可追溯情况，不代表结论正确率。",
    "comparison.eyebrow": "独立工作流",
    "comparison.title": "多地址同口径比较",
    "comparison.noScore": "只排序原始指标，不判断好坏",
    "comparison.addresses": "每行一个公开地址（2至20个）",
    "comparison.scope": "比较商圈",
    "comparison.action": "开始比较",
    "comparison.ready": "比较只展示同一指标的原始值和顺序，不生成未经校准的综合分数。",
    "comparison.empty": "完成比较后显示人口、岗位、竞争、交通和房地产案例等同口径结果。",
    "comparison.invalidCount": "请输入 2 至 20 个地址，每行一个。",
    "comparison.loading": "正在以 {scope} 同口径分析 {count} 个地址……",
    "comparison.failure": "比较失败，请稍后重试。",
    "comparison.success": "比较完成：{sites} 个地址、{metrics} 个同口径指标；未生成综合分数。",
    "comparison.address": "地址",
    "comparison.rank": "高到低第 {rank}",
    "comparison.missing": "缺失 {count}",
    "comparison.warning": "排序仅描述当前快照中的原始值，不代表更优，也不是投资建议。",
    "privacy.title": "数据使用边界",
    "privacy.body": "区域统计不代表具体个人。ACS 是调查估计并显示误差范围；LODES 统计岗位；游客和客群为模型代理。自动综合评分在真实项目校准前保持关闭。",
    "footer.copy": "本地商业情报数据库 · 数据与计算均可追溯",
    "export.label": "导出报告",
    "theme.toDark": "切换深色模式",
    "theme.toLight": "切换浅色模式",
    "language.toEnglish": "Switch to English",
    "language.toChinese": "切换到中文",
    "identity.walk": "{minutes} 分钟步行 · 网络阈值 {distance}",
    "identity.radius": "直线范围 {distance}",
    "identity.snapshot": "年份随快照",
    "metric.confidence": "置信度",
    "metric.percentile": "NYC同口径第{value}百分位",
    "metric.evidence": "证据 {count} 条",
    "metric.percentagePoints": "个百分点",
    "metric.noData": "当前快照无可用指标",
    "metric.derived": "衍生指标",
    "catchment.250m": "250米",
    "catchment.500m": "500米",
    "catchment.1km": "1公里",
    "catchment.5min_walk": "5分钟步行",
    "catchment.10min_walk": "10分钟步行",
    "catchment.15min_walk": "15分钟步行"
  },
  en: { "assistant.title": 'Describe a two-address comparison',
  "assistant.hint": 'Put each address in quotes. Confirm the full-service restaurant type and radial scope before querying data. Optional AI is off by default.',
  "assistant.request": 'Requirements', "assistant.interpret": 'Prepare comparison',
  "assistant.notice": 'The quoted Demo Avenue addresses work only in the synthetic demo. Use actual addresses with your local data.',
  "assistant.first": 'First address', "assistant.second": 'Second address', "assistant.choose": 'Choose supported type',
  "assistant.scope": 'Radial scope', "assistant.confirm": 'Confirm and compare',
    "accessibility.skip": "Skip to analysis workspace",
    "accessibility.primaryNav": "Primary navigation",
    "accessibility.export": "Export analysis",
    "accessibility.comparisonExport": "Export comparison",
    "accessibility.tabs": "Analysis sections",
    "accessibility.theme": "Toggle color theme",
    "nav.single": "Site analysis",
    "nav.compare": "Compare sites",
    "nav.evidence": "Data guidance",
    "header.local": "Local only",
    "header.eyebrow": "LOCAL · TRACEABLE · GREATER NYC",
    "header.title": "Commercial Location Intelligence",
    "form.address": "Address",
    "form.addressPlaceholder": "Enter a public address in Greater New York",
    "form.businessType": "Business type",
    "form.focusArea": "Trade area in view",
    "form.analyze": "Analyze site",
    "business.fullService": "Full-service restaurant",
    "status.ready": "The system uses the local database. A first-time walking area may read the public OSM network.",
    "status.noCatchment": "Select at least one trade area.",
    "status.analyzing": "Standardizing the address, generating trade areas, and joining local data…",
    "status.success": "Analysis complete. {count} items are missing; every metric retains its source, year, type, and confidence.",
    "status.failure": "Analysis failed. Please try again.",
    "status.settingsChanged": "Trade-area settings changed. Run the analysis again to generate those results.",
    "status.invalidResponse": "The server returned an unreadable result. Run the analysis again.",
    "status.httpFailure": "The server could not complete the request (HTTP {status}). Please try again.",
    "filters.summary": "Filters & trade areas",
    "filters.eyebrow": "ANALYSIS SETTINGS",
    "filters.title": "Trade-area scope",
    "filters.multi": "Multi-select",
    "filters.distance": "Distance bands",
    "filters.radiusNote": "Projected radial buffers",
    "filters.walk": "Walking areas",
    "filters.walkNote": "Street-network based",
    "filters.methodTitle": "Method note",
    "filters.methodBody": "Distance bands use a suitable projected CRS for New York. Walking areas are generated from the locally stored street network, never represented as simple circles.",
    "filters.reset": "Restore default scope",
    "map.eyebrow": "SPATIAL VIEW",
    "map.title": "Site & trade areas",
    "map.awaiting": "Awaiting analysis",
    "map.comparison": "Comparing {count} sites · {scope}",
    "map.synthetic": "Synthetic demo",
    "map.aria": "New York map and trade areas",
    "map.attribution": "Basemap © OpenStreetMap contributors. Trade-area geometry and results remain local.",
    "tabs.overview": "Overview",
    "tabs.audience": "Audience",
    "tabs.competition": "Competition",
    "tabs.mobility": "Mobility",
    "tabs.property": "Property & risk",
    "tabs.evidence": "Data evidence",
    "details.title": "Metrics and evidence",
    "details.empty": "Run an analysis to show the standardized address, parcel, trade area, and data version.",
    "details.noData": "Current trade-area data appears after analysis.",
    "details.audience": "Audience profile",
    "details.visitorHealth": "Visitors, health & environment",
    "details.demographics": "Residents, income & demographics",
    "details.competition": "Competition, substitutes & demand complements",
    "details.mobility": "Mobility & daytime workforce",
    "details.property": "Real estate, development & regulation",
    "details.sources": "Sources, years & confidence",
    "decision.eyebrow": "DECISION READING",
    "decision.title": "Site decision summary",
    "decision.noScore": "No composite score",
    "decision.emptyTitle": "Awaiting site analysis",
    "decision.emptyBody": "After analysis, real metrics will be summarized into site context, opportunities, risks, and evidence coverage.",
    "decision.location": "Site context",
    "decision.opportunities": "Key opportunities",
    "decision.risks": "Key risks",
    "decision.evidence": "Evidence coverage",
    "decision.address": "Standardized address",
    "decision.area": "Area",
    "decision.parcel": "Parcel",
    "decision.scope": "Trade area",
    "decision.unmatched": "Not matched",
    "decision.marketProfile": "Opportunity profile: {value}",
    "decision.audienceSignal": "Audience classification is “{value},” based on transparent proxy indicators.",
    "decision.populationHigh": "Resident population is at the {percentile}th percentile for the comparable NYC benchmark.",
    "decision.jobsHigh": "Daytime jobs are at the {percentile}th percentile for the comparable NYC benchmark.",
    "decision.transit": "The trade area contains {subway} subway complexes and {bus} bus stops.",
    "decision.complements": "{count} demand-supporting facilities are identified in the trade area.",
    "decision.opportunityFallback": "The current snapshot does not form a strong opportunity signal. Review the detailed metrics and field conditions.",
    "decision.competitionSignal": "Competition pressure: {value}.",
    "decision.regulatorySignal": "Regulatory-record screen: {value}.",
    "decision.environmentSignal": "Environmental overlap screen: {value}.",
    "decision.missing": "{count} items remain missing from the current result.",
    "decision.riskFallback": "Public data shows no prominent risk signal in this snapshot. Lease terms, site conditions, and current regulatory records still require verification.",
    "decision.traceability": "Source and year coverage",
    "decision.confidence": "Confidence-field coverage",
    "decision.sources": "Distinct data sources",
    "decision.sourceCount": "{count}",
    "decision.evidenceNote": "Coverage describes traceability, not the probability that a conclusion is correct.",
    "comparison.eyebrow": "SEPARATE WORKFLOW",
    "comparison.title": "Comparable multi-site analysis",
    "comparison.noScore": "Ranks raw metrics without judging quality",
    "comparison.addresses": "One public address per line (2–20)",
    "comparison.scope": "Comparison trade area",
    "comparison.action": "Compare sites",
    "comparison.ready": "The comparison displays like-for-like raw values and order only. It does not generate an uncalibrated composite score.",
    "comparison.empty": "Run a comparison to display like-for-like population, jobs, competition, mobility, and real-estate case metrics.",
    "comparison.invalidCount": "Enter 2 to 20 addresses, one per line.",
    "comparison.loading": "Analyzing {count} addresses using the same {scope} trade area…",
    "comparison.failure": "Comparison failed. Please try again.",
    "comparison.success": "Comparison complete: {sites} sites and {metrics} like-for-like metrics. No composite score was generated.",
    "comparison.address": "Address",
    "comparison.rank": "Rank {rank}, high to low",
    "comparison.missing": "{count} missing",
    "comparison.warning": "Ordering describes raw values in the current snapshot. It does not mean better and is not investment advice.",
    "privacy.title": "Data-use boundary",
    "privacy.body": "Area statistics do not describe specific people. ACS values are survey estimates with margins of error; LODES counts jobs; visitor and audience outputs are model proxies. Composite scoring remains disabled until calibrated against real projects.",
    "footer.copy": "Local commercial-intelligence database · Traceable data and calculations",
    "export.label": "Export report",
    "theme.toDark": "Switch to dark mode",
    "theme.toLight": "Switch to light mode",
    "language.toEnglish": "Switch to English",
    "language.toChinese": "切换到中文",
    "identity.walk": "{minutes}-minute walk · network threshold {distance}",
    "identity.radius": "Radial distance {distance}",
    "identity.snapshot": "Year follows snapshot",
    "metric.confidence": "Confidence",
    "metric.percentile": "{value}th NYC comparable percentile",
    "metric.evidence": "{count} evidence records",
    "metric.percentagePoints": " percentage points",
    "metric.noData": "No metric is available in the current snapshot",
    "metric.derived": "Derived",
    "catchment.250m": "250 m",
    "catchment.500m": "500 m",
    "catchment.1km": "1 km",
    "catchment.5min_walk": "5-minute walk",
    "catchment.10min_walk": "10-minute walk",
    "catchment.15min_walk": "15-minute walk"
  }
};

const METRIC_LABELS = {
  asking_rent: ["挂牌租金", "Asking rent"],
  resident_population: ["常住人口", "Resident population"],
  resident_population_density_per_sq_mile: ["人口密度（每平方英里）", "Population density (per sq mi)"],
  median_household_income: ["家庭收入中位数", "Median household income"],
  per_capita_income: ["人均收入", "Per-capita income"],
  median_age: ["年龄中位数", "Median age"],
  age_under_25: ["25岁以下", "Under age 25"],
  age_25_44: ["25–44岁", "Age 25–44"],
  age_45_64: ["45–64岁", "Age 45–64"],
  age_65_plus: ["65岁以上", "Age 65+"],
  daytime_jobs: ["白天岗位", "Daytime jobs"],
  top_workplace_industries: ["主要工作行业", "Top workplace industries"],
  direct_competitor_count: ["直接竞争者", "Direct competitors"],
  substitute_count: ["替代品", "Substitutes"],
  demand_complement_count: ["需求补充设施", "Demand complements"],
  closed_competitor_count: ["关闭状态记录", "Closed competitor records"],
  competitors_per_1000_residents: ["每千名居民竞争者", "Competitors per 1,000 residents"],
  competitors_per_1000_jobs: ["每千个岗位竞争者", "Competitors per 1,000 jobs"],
  historical_transaction_case_count: ["历史公开交易案例", "Historical public transaction cases"],
  historical_transaction_amount_median: ["历史案例金额中位数", "Median historical case amount"],
  rolling_sale_record_count: ["近十二个月成交记录", "Rolling 12-month sale records"],
  rolling_sale_price_median: ["近十二个月成交中位数", "Median rolling sale price"],
  building_permit_issued_24m_count: ["近24个月建筑许可记录", "Building permits issued, 24 months"],
  acris_deed_document_count_24m: ["近24个月ACRIS契据", "ACRIS deed documents, 24 months"],
  parcel_count: ["相交税地块", "Intersecting tax lots"],
  building_floor_area_sqft: ["建筑楼面面积", "Building floor area"],
  residential_floor_area_sqft: ["住宅楼面面积", "Residential floor area"],
  office_floor_area_sqft: ["办公楼面面积", "Office floor area"],
  retail_floor_area_sqft: ["零售楼面面积", "Retail floor area"],
  regional_rail_station_count: ["区域铁路站点", "Regional rail stations"],
  regional_transit_poi_count: ["区域交通设施代理", "Regional transit-place proxies"],
  regional_hotel_poi_count: ["区域住宿设施代理", "Regional lodging proxies"],
  regional_school_poi_count: ["区域学校代理", "Regional school proxies"],
  regional_healthcare_poi_count: ["区域医疗设施代理", "Regional healthcare proxies"],
  regional_park_poi_count: ["区域公园代理", "Regional park proxies"],
  regional_library_poi_count: ["区域图书馆代理", "Regional library proxies"],
  subway_complex_count: ["地铁站复合体", "Subway complexes"],
  subway_entries_2025: ["2025地铁进站量", "2025 subway entries"],
  subway_entrance_exit_count: ["地铁出入口物理点", "Subway entrance/exit points"],
  subway_entry_allowed_count: ["允许进站入口点", "Entry-enabled subway entrances"],
  bus_stop_count: ["公交停靠点", "Bus stops"],
  citibike_station_count: ["Citi Bike站点", "Citi Bike stations"],
  citibike_bikes_available_current: ["当前可借自行车", "Bikes currently available"],
  citibike_docks_available_current: ["当前可还空位", "Docks currently available"],
  tlc_pickup_trips_average_daily_area_weighted: ["TLC日均上车估计", "Estimated average daily TLC pickups"],
  tlc_dropoff_trips_average_daily_area_weighted: ["TLC日均下车估计", "Estimated average daily TLC drop-offs"],
  pedestrian_count_latest_median: ["最新行人计数中位数", "Latest median pedestrian count"],
  traffic_speed_current_median_mph: ["当前道路速度中位数", "Current median road speed"],
  hotel_count: ["酒店物业", "Hotel properties"],
  attraction_count: ["景点与文化设施", "Attractions and cultural facilities"],
  nightlife_location_count: ["夜生活场所", "Nightlife locations"],
  audience_type: ["客群类型", "Audience type"],
  resident_index: ["居民指数", "Resident index"],
  worker_index: ["工作者指数", "Worker index"],
  visitor_proxy_index: ["游客代理指数", "Visitor proxy index"],
  nightlife_index: ["夜生活指数", "Nightlife index"],
  transit_hub_index: ["交通枢纽指数", "Transit-hub index"],
  pm25_indicator: ["PM2.5 指标", "PM2.5 indicator"],
  asthma_health_indicator: ["哮喘健康指标", "Asthma health indicator"],
  cdc_places_current_asthma_pct: ["CDC成人当前哮喘估计", "CDC adult current-asthma estimate"],
  cdc_places_diagnosed_diabetes_pct: ["CDC成人糖尿病估计", "CDC diagnosed-diabetes estimate"],
  cdc_places_obesity_pct: ["CDC成人肥胖估计", "CDC adult-obesity estimate"],
  cdc_places_uninsured_pct: ["CDC成人无医保估计", "CDC uninsured-adult estimate"],
  cdc_places_food_insecurity_pct: ["CDC成人食物不安全估计", "CDC adult food-insecurity estimate"],
  hurricane_evacuation_overlap_pct: ["飓风疏散区覆盖", "Hurricane evacuation-zone overlap"],
  future_2050_floodplain_overlap_pct: ["2050年代洪泛区覆盖", "2050s floodplain overlap"],
  sea_level_2100_floodplain_overlap_pct: ["2100年洪泛区覆盖", "2100 sea-level floodplain overlap"],
  fema_special_flood_hazard_overlap_pct: ["FEMA特殊洪水危险区覆盖", "FEMA special flood-hazard overlap"],
  environmental_exposure_profile: ["环境暴露画像", "Environmental exposure profile"],
  regulatory_risk_signal: ["监管记录筛查信号", "Regulatory-record screening signal"],
  active_dob_violation_count: ["现行DOB违规记录", "Active DOB violations"],
  active_dob_violations_per_100_parcels: ["每百地块现行DOB违规记录", "Active DOB violations per 100 parcels"],
  active_ecb_violation_count: ["现行DOB-ECB传票记录", "Active DOB-ECB summonses"],
  active_ecb_violations_per_100_parcels: ["每百地块现行DOB-ECB传票记录", "Active DOB-ECB summonses per 100 parcels"],
  public_transit_commute_share: ["公共交通通勤占比", "Public-transit commute share"],
  bachelors_or_higher_share: ["本科及以上占比", "Bachelor's degree or higher share"],
  foreign_born_share: ["外国出生人口占比", "Foreign-born share"],
  spanish_language_share: ["西班牙语使用人口占比", "Spanish-language share"],
  owner_occupied_share: ["自有住房占比", "Owner-occupied housing share"],
  renter_occupied_share: ["租住住房占比", "Renter-occupied housing share"],
  no_vehicle_household_share: ["无车家庭占比", "Households without a vehicle"],
  family_household_share: ["家庭户占比", "Family-household share"],
  nonfamily_household_share: ["非家庭户占比", "Nonfamily-household share"],
  civilian_labor_force_employment_rate: ["劳动力就业率", "Civilian labor-force employment rate"],
  civilian_labor_force_unemployment_rate: ["劳动力失业率", "Civilian labor-force unemployment rate"],
  non_hispanic_white_share: ["非西语裔白人人口占比", "Non-Hispanic White share"],
  non_hispanic_black_share: ["非西语裔黑人人口占比", "Non-Hispanic Black share"],
  non_hispanic_asian_share: ["非西语裔亚裔人口占比", "Non-Hispanic Asian share"],
  hispanic_or_latino_share: ["西语裔或拉丁裔人口占比", "Hispanic or Latino share"],
  english_only_language_share: ["仅英语人口占比", "English-only language share"],
  other_indo_european_language_share: ["其他印欧语言人口占比", "Other Indo-European language share"],
  asian_pacific_language_share: ["亚太语言人口占比", "Asian and Pacific language share"],
  drove_alone_commute_share: ["单独驾车通勤占比", "Drove-alone commute share"],
  walked_commute_share: ["步行通勤占比", "Walked-to-work share"],
  worked_from_home_share: ["居家办公占比", "Worked-from-home share"],
  management_business_science_arts_occupation_share: ["管理、商业、科学与艺术职业占比", "Management, business, science and arts occupation share"],
  service_occupation_share: ["服务职业占比", "Service-occupation share"],
  sales_office_occupation_share: ["销售与办公室职业占比", "Sales and office-occupation share"],
  demand_support_signal: ["需求支撑信号", "Demand-support signal"],
  competition_pressure_signal: ["竞争压力信号", "Competition-pressure signal"],
  market_opportunity_profile: ["机会画像", "Opportunity profile"],
  decennial_population_2020: ["2020年十年普查人口", "2020 Decennial Census population"],
  building_footprint_count: ["建筑轮廓数量", "Building footprints"],
  intersecting_lot_area_sqft: ["相交地块面积", "Intersecting lot area"],
  total_units: ["总单元数", "Total units"],
  residential_units: ["住宅单元数", "Residential units"],
  building_floor_area_density: ["建筑楼面面积密度", "Building floor-area density"],
  residential_floor_area_density: ["住宅楼面面积密度", "Residential floor-area density"],
  office_floor_area_density: ["办公楼面面积密度", "Office floor-area density"],
  retail_floor_area_density: ["零售楼面面积密度", "Retail floor-area density"],
  rolling_sales_per_100_parcels: ["每百地块近十二个月成交", "Rolling sales per 100 parcels"],
  acris_document_record_count_24m: ["近24个月ACRIS文件记录", "ACRIS document records, 24 months"],
  acris_document_records_per_100_parcels_24m: ["每百地块近24个月ACRIS文件", "ACRIS documents per 100 parcels, 24 months"],
  acris_mortgage_document_count_24m: ["近24个月ACRIS抵押文件", "ACRIS mortgage documents, 24 months"],
  certificate_of_occupancy_issued_24m_count: ["近24个月入住证书", "Certificates of occupancy issued, 24 months"],
  legal_short_term_rental_count: ["合法短租登记", "Legal short-term rental registrations"],
  alcohol_license_count: ["酒类许可", "Alcohol licenses"],
  school_count: ["学校设施", "School facilities"],
  health_facility_count: ["医疗设施", "Health facilities"],
  park_facility_count: ["公园设施", "Park facilities"],
  culture_and_library_count: ["文化与图书馆设施", "Cultural and library facilities"],
  public_facility_count: ["公共设施", "Public facilities"],
  vehicle_volume_median_15min: ["15分钟车流量中位数", "Median 15-minute vehicle volume"],
  vehicle_volume_monitor_count: ["车流监测路段", "Vehicle-volume monitoring segments"],
  pedestrian_monitor_count: ["行人监测点", "Pedestrian monitoring locations"],
  traffic_speed_link_count: ["道路速度监测路段", "Traffic-speed monitored links"],
  tlc_taxi_zone_count: ["相交TLC出租车分区", "Intersecting TLC taxi zones"],
  tlc_pickup_trips_monthly_area_weighted: ["TLC月度上车估计", "Estimated monthly TLC pickups"],
  tlc_dropoff_trips_monthly_area_weighted: ["TLC月度下车估计", "Estimated monthly TLC drop-offs"],
  "311_request_count_24m": ["近24个月311请求", "311 requests, 24 months"],
  "311_requests_per_100_parcels_24m": ["每百地块近24个月311请求", "311 requests per 100 parcels, 24 months"],
  "311_noise_request_count_24m": ["近24个月311噪音请求", "311 noise requests, 24 months"],
  "311_noise_requests_per_100_parcels_24m": ["每百地块近24个月311噪音请求", "311 noise requests per 100 parcels, 24 months"],
  natural_resources_construction_maintenance_occupation_share: ["自然资源、建筑与维护职业占比", "Natural resources, construction and maintenance occupation share"],
  production_transport_material_moving_occupation_share: ["生产、运输与物料搬运职业占比", "Production, transportation and material-moving occupation share"]
};

const TEXT_VALUES_EN = {
  "证据不足": "Insufficient evidence",
  "混合型": "Mixed",
  "居民主导": "Resident-led",
  "上班族主导": "Worker-led",
  "游客主导": "Visitor-led",
  "夜生活主导": "Nightlife-led",
  "交通换乘型": "Transit-hub oriented",
  "未识别到覆盖重叠": "No mapped overlap identified",
  "低（未关联到现行违规记录）": "Low (no active violation record linked)"
};

const escapeHtml = (value) => String(value == null ? "" : value).replace(
  /[&<>'"]/g,
  (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", "\"": "&quot;" })[character]
);

const safeStorageGet = (key) => {
  try { return localStorage.getItem(key); } catch (_) { return null; }
};
const safeStorageSet = (key, value) => {
  try { localStorage.setItem(key, value); } catch (_) {}
};

let currentLanguage = safeStorageGet("nyc-lens-language") || "en";
let currentTheme = document.documentElement.dataset.theme === "dark" ? "dark" : "light";
let currentResult = null;
let currentComparison = null;
let marker = null;
const comparisonMarkers = [];
let mapView = "site";
let pendingMapRender = null;
let statusState = { key: "status.ready", params: {}, tone: "ready", raw: null };
let comparisonStatusState = { key: "comparison.ready", params: {}, tone: "ready", raw: null };

const t = (key, params) => {
  const dictionary = COPY[currentLanguage] || COPY.zh;
  let text = dictionary[key] || COPY.zh[key] || key;
  Object.entries(params || {}).forEach(([name, value]) => {
    text = text.replaceAll("{" + name + "}", String(value));
  });
  return text;
};

const metricLabel = (id) => {
  const label = METRIC_LABELS[id];
  if (!label) return id.replaceAll("_", " ");
  return label[currentLanguage === "zh" ? 0 : 1];
};

const catchmentLabel = (id) => t("catchment." + id);
const locale = () => currentLanguage === "zh" ? "zh-CN" : "en-US";

const translateTextValue = (value) => {
  if (value == null || currentLanguage === "zh") return value;
  if (TEXT_VALUES_EN[value]) return TEXT_VALUES_EN[value];
  let match = String(value).match(/^([高中低])（组成指标中位百分位 ([0-9.]+)）$/);
  if (match) {
    const band = { "高": "High", "中": "Medium", "低": "Low" }[match[1]];
    return band + " (component median percentile " + match[2] + ")";
  }
  match = String(value).match(/^需求支撑([高中低])、竞争压力([高中低])$/);
  if (match) {
    const bands = { "高": "high", "中": "medium", "低": "low" };
    return "Demand support " + bands[match[1]] + "; competition pressure " + bands[match[2]];
  }
  match = String(value).match(/^(局部重叠|显著重叠|大范围重叠)（最大覆盖 ([0-9.]+)%）$/);
  if (match) {
    const levels = { "局部重叠": "Localized overlap", "显著重叠": "Material overlap", "大范围重叠": "Broad overlap" };
    return levels[match[1]] + " (maximum coverage " + match[2] + "%)";
  }
  if (String(value).startsWith("{") && String(value).endsWith("}")) {
    try {
      const parsed = JSON.parse(value);
      return Object.entries(parsed).map(([key, number]) => key + ": " + new Intl.NumberFormat(locale()).format(number)).join(", ");
    } catch (_) {}
  }
  return value;
};

const formatMetric = (metric) => {
  if (!metric) return "—";
  if (metric.text_value) return translateTextValue(metric.text_value);
  if (metric.value === null || metric.value === undefined) return "—";
  if (metric.unit === "share") return (metric.value * 100).toFixed(1) + "%";
  if (metric.unit === "percent") return Number(metric.value).toFixed(1) + "%";
  if (metric.unit === "USD") {
    return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(metric.value);
  }
  if (String(metric.unit || "").includes("index")) return Number(metric.value).toFixed(0);
  return new Intl.NumberFormat(locale(), { maximumFractionDigits: Math.abs(metric.value) < 10 ? 2 : 0 }).format(metric.value);
};

const form = document.querySelector("#analysis-form");
const statusEl = document.querySelector("#status");
const statusText = document.querySelector("[data-status-text]");
const button = document.querySelector("#analyze-button");
const catchmentSelect = document.querySelector("#catchment-select");
const languageToggle = document.querySelector("#language-toggle");
const themeToggle = document.querySelector("#theme-toggle");
const comparisonForm = document.querySelector("#comparison-form");
const comparisonStatus = document.querySelector("#comparison-status");
const comparisonStatusText = comparisonStatus.querySelector("span:last-child");
const compareButton = document.querySelector("#compare-button");
const filterPanel = document.querySelector(".filter-panel");
const desktopFilterQuery = window.matchMedia("(min-width: 1351px)");

const syncFilterPanelLayout = (event) => {
  filterPanel.open = event.matches;
};

syncFilterPanelLayout(desktopFilterQuery);
desktopFilterQuery.addEventListener("change", syncFilterPanelLayout);

const map = new maplibregl.Map({
  container: "map",
  center: [-73.94, 40.73],
  zoom: 10.2,
  style: {
    version: 8,
    sources: {
      osm: {
        type: "raster",
        tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
        tileSize: 256,
        attribution: "© OpenStreetMap contributors"
      }
    },
    layers: [{ id: "osm", type: "raster", source: "osm" }]
  }
});
map.addControl(new maplibregl.NavigationControl(), "top-right");
map.on("load", () => {
  if (pendingMapRender) {
    renderMap(pendingMapRender.result, pendingMapRender.selectedCatchment);
    pendingMapRender = null;
  }
});

const setStatus = (key, params, tone, raw) => {
  statusState = { key, params: params || {}, tone: tone || "ready", raw: raw || null };
  statusEl.classList.toggle("loading", statusState.tone === "loading");
  statusEl.classList.toggle("error", statusState.tone === "error");
  statusText.textContent = statusState.raw || t(statusState.key, statusState.params);
};

const setComparisonStatus = (key, params, tone, raw) => {
  comparisonStatusState = { key, params: params || {}, tone: tone || "ready", raw: raw || null };
  comparisonStatus.classList.toggle("loading", comparisonStatusState.tone === "loading");
  comparisonStatus.classList.toggle("error", comparisonStatusState.tone === "error");
  comparisonStatusText.textContent = comparisonStatusState.raw || t(comparisonStatusState.key, comparisonStatusState.params);
};

const selectedCatchments = () => [...document.querySelectorAll("input[name=catchment]:checked")].map((item) => item.value);

const syncCatchmentControls = (preferred, allowedValues) => {
  const checked = selectedCatchments();
  const values = allowedValues || checked;
  const previous = preferred || catchmentSelect.value;
  catchmentSelect.innerHTML = values.map((value) => (
    "<option value=\"" + escapeHtml(value) + "\">" + escapeHtml(catchmentLabel(value)) + "</option>"
  )).join("");
  if (values.includes(previous)) catchmentSelect.value = previous;
  else if (values.includes("10min_walk")) catchmentSelect.value = "10min_walk";
  else if (values.length) catchmentSelect.value = values[0];
  document.querySelectorAll("[data-catchment-label]").forEach((element) => {
    element.textContent = catchmentLabel(element.dataset.catchmentLabel);
  });
  document.querySelector("#filter-summary").textContent = checked.length
    ? checked.map(catchmentLabel).join(" · ")
    : t("status.noCatchment");
};

const applyTheme = () => {
  document.documentElement.dataset.theme = currentTheme;
  document.querySelector(".theme-glyph").textContent = currentTheme === "dark" ? "☀" : "☾";
  themeToggle.setAttribute("aria-label", t(currentTheme === "dark" ? "theme.toLight" : "theme.toDark"));
  themeToggle.setAttribute("aria-pressed", String(currentTheme === "dark"));
  if (map.getLayer("catchment-fill")) {
    map.setPaintProperty("catchment-fill", "fill-opacity", ["case", ["get", "active"], currentTheme === "dark" ? 0.36 : 0.28, currentTheme === "dark" ? 0.13 : 0.08]);
  }
  map.triggerRepaint();
};

const renderExports = () => {
  const exports = document.querySelector("#exports");
  if (!currentResult) {
    exports.hidden = true;
    exports.innerHTML = "";
    return;
  }
  const links = ["json", "csv", "xlsx", "html"].map((format) => (
    "<a href=\"/api/site/" + encodeURIComponent(currentResult.site_id) + "/report?format=" + format + "\">" + format.toUpperCase() + "</a>"
  )).join("");
  exports.hidden = false;
  exports.innerHTML = "<details><summary>" + escapeHtml(t("export.label")) + "</summary><div class=\"export-popover\">" + links + "</div></details>";
};

const applyLanguage = () => {
  document.documentElement.lang = currentLanguage === "zh" ? "zh-CN" : "en";
  document.title = currentLanguage === "zh" ? "NYC Lens｜纽约商圈情报" : "NYC Lens | Commercial Location Intelligence";
  document.querySelectorAll("[data-i18n]").forEach((element) => {
    if (element.hasAttribute("data-status-text")) return;
    element.textContent = t(element.dataset.i18n);
  });
  document.querySelectorAll("[data-i18n-placeholder]").forEach((element) => {
    element.setAttribute("placeholder", t(element.dataset.i18nPlaceholder));
  });
  document.querySelectorAll("[data-i18n-aria]").forEach((element) => {
    element.setAttribute("aria-label", t(element.dataset.i18nAria));
  });
  languageToggle.textContent = currentLanguage === "zh" ? "EN" : "中文";
  languageToggle.setAttribute("aria-label", t(currentLanguage === "zh" ? "language.toEnglish" : "language.toChinese"));
  syncCatchmentControls(catchmentSelect.value, currentResult ? Object.keys(currentResult.catchments) : undefined);
  setStatus(statusState.key, statusState.params, statusState.tone, statusState.raw);
  setComparisonStatus(comparisonStatusState.key, comparisonStatusState.params, comparisonStatusState.tone, comparisonStatusState.raw);
  applyTheme();
  renderExports();
  if (currentResult) renderResult(currentResult, catchmentSelect.value, mapView === "site");
  if (currentComparison) renderComparison(currentComparison);
  if (mapView === "comparison" && currentComparison) renderComparisonMapContext(currentComparison);
};

const readApiPayload = async (response) => {
  const body = await response.text();
  if (!body) return {};
  try {
    return JSON.parse(body);
  } catch (_) {
    if (!response.ok) throw new Error(t("status.httpFailure", { status: response.status }));
    throw new Error(t("status.invalidResponse"));
  }
};

const apiErrorMessage = (payload, fallback) => {
  const detail = payload && payload.detail;
  if (typeof detail === "string" && detail) return detail;
  if (detail && typeof detail === "object") {
    const localized = currentLanguage === "zh" ? detail.message_zh : detail.message_en;
    if (localized) return localized;
    if (detail.message) return detail.message;
  }
  if (Array.isArray(detail)) {
    const messages = detail.map((item) => item && item.msg).filter(Boolean);
    if (messages.length) return messages.join(currentLanguage === "zh" ? "；" : "; ");
  }
  return fallback;
};

const metricList = (metrics, ids) => {
  const items = ids.filter((id) => metrics[id]).map((id) => {
    const metric = metrics[id];
    const moeValue = metric.unit === "share" ? metric.margin_of_error * 100 : metric.margin_of_error;
    const moeUnit = metric.unit === "share" || metric.unit === "percent" ? t("metric.percentagePoints") : "";
    const moe = metric.margin_of_error != null
      ? " · MOE ±" + new Intl.NumberFormat(locale(), { maximumFractionDigits: 1 }).format(moeValue) + moeUnit
      : "";
    const percentile = metric.percentile == null ? "" : " · " + t("metric.percentile", { value: metric.percentile.toFixed(0) });
    const evidence = metric.evidence_count == null ? "" : " · " + t("metric.evidence", { count: new Intl.NumberFormat(locale()).format(metric.evidence_count) });
    const source = metric.source_id || t("metric.derived");
    const year = metric.source_year || t("identity.snapshot");
    const confidence = metric.confidence == null ? "—" : Math.round(metric.confidence * 100) + "%";
    return "<li><span>" + escapeHtml(metricLabel(id)) + "</span><b>" + escapeHtml(formatMetric(metric)) + "</b><small>"
      + escapeHtml(source) + " · " + escapeHtml(year) + " · " + escapeHtml(t("metric.confidence")) + " " + confidence
      + percentile + moe + evidence + "</small></li>";
  });
  return "<ul class=\"metric-list\">" + (items.join("") || "<li>" + escapeHtml(t("metric.noData")) + "</li>") + "</ul>";
};

const renderDecisionSummary = (result, selectedCatchment, metrics) => {
  document.querySelector("#decision-empty").hidden = true;
  document.querySelector("#decision-content").hidden = false;
  const area = [result.subregion, result.borough, result.county_name].filter(Boolean).filter((value, index, list) => list.indexOf(value) === index).join(" · ") || "—";
  document.querySelector("#location-summary").innerHTML = "<dl class=\"location-list\">"
    + "<dt>" + escapeHtml(t("decision.address")) + "</dt><dd>" + escapeHtml(result.standardized_address || result.input_address) + "</dd>"
    + "<dt>" + escapeHtml(t("decision.area")) + "</dt><dd>" + escapeHtml(area) + "</dd>"
    + "<dt>" + escapeHtml(t("decision.parcel")) + "</dt><dd>" + escapeHtml((result.parcel && result.parcel.bbl) || t("decision.unmatched")) + "</dd>"
    + "<dt>" + escapeHtml(t("decision.scope")) + "</dt><dd>" + escapeHtml(catchmentLabel(selectedCatchment)) + "</dd>"
    + "</dl>";

  const opportunities = [];
  if (metrics.market_opportunity_profile && metrics.market_opportunity_profile.text_value) {
    opportunities.push(t("decision.marketProfile", { value: formatMetric(metrics.market_opportunity_profile) }));
  }
  if (metrics.audience_type && metrics.audience_type.text_value) {
    opportunities.push(t("decision.audienceSignal", { value: formatMetric(metrics.audience_type) }));
  }
  if (metrics.resident_population && metrics.resident_population.percentile >= 67) {
    opportunities.push(t("decision.populationHigh", { percentile: Math.round(metrics.resident_population.percentile) }));
  }
  if (metrics.daytime_jobs && metrics.daytime_jobs.percentile >= 67) {
    opportunities.push(t("decision.jobsHigh", { percentile: Math.round(metrics.daytime_jobs.percentile) }));
  }
  const subway = metrics.subway_complex_count && metrics.subway_complex_count.value;
  const bus = metrics.bus_stop_count && metrics.bus_stop_count.value;
  if ((subway || 0) > 0 || (bus || 0) > 0) {
    opportunities.push(t("decision.transit", {
      subway: new Intl.NumberFormat(locale()).format(subway || 0),
      bus: new Intl.NumberFormat(locale()).format(bus || 0)
    }));
  }
  if (metrics.demand_complement_count && metrics.demand_complement_count.value > 0) {
    opportunities.push(t("decision.complements", { count: new Intl.NumberFormat(locale()).format(metrics.demand_complement_count.value) }));
  }
  document.querySelector("#opportunities").innerHTML = "<ul class=\"summary-list\"><li>"
    + (opportunities.slice(0, 4).map(escapeHtml).join("</li><li>") || escapeHtml(t("decision.opportunityFallback")))
    + "</li></ul>";

  const risks = [];
  if (metrics.competition_pressure_signal && metrics.competition_pressure_signal.text_value) {
    risks.push(t("decision.competitionSignal", { value: formatMetric(metrics.competition_pressure_signal) }));
  }
  if (metrics.regulatory_risk_signal && metrics.regulatory_risk_signal.text_value) {
    risks.push(t("decision.regulatorySignal", { value: formatMetric(metrics.regulatory_risk_signal) }));
  }
  if (metrics.environmental_exposure_profile && metrics.environmental_exposure_profile.text_value) {
    risks.push(t("decision.environmentSignal", { value: formatMetric(metrics.environmental_exposure_profile) }));
  }
  if ((result.missing_data || []).length) {
    risks.push(t("decision.missing", { count: result.missing_data.length }));
  }
  document.querySelector("#risks").innerHTML = "<ul class=\"summary-list\"><li>"
    + (risks.slice(0, 4).map(escapeHtml).join("</li><li>") || escapeHtml(t("decision.riskFallback")))
    + "</li></ul>";

  const usable = Object.values(metrics).filter((metric) => metric && (metric.value !== null && metric.value !== undefined || metric.text_value));
  const traceable = usable.filter((metric) => metric.source_id && metric.source_year);
  const confidenceReady = usable.filter((metric) => metric.confidence !== null && metric.confidence !== undefined);
  const sourceCount = new Set(usable.map((metric) => metric.source_id).filter((source) => source && source !== "derived")).size;
  const traceabilityPct = usable.length ? Math.round(traceable.length / usable.length * 100) : 0;
  const confidencePct = usable.length ? Math.round(confidenceReady.length / usable.length * 100) : 0;
  const evidenceRow = (label, value, barValue) => "<div class=\"evidence-row\"><span>" + escapeHtml(label) + "</span><b>" + escapeHtml(value) + "</b><div class=\"evidence-bar\"><i style=\"width:" + Math.max(0, Math.min(100, barValue)) + "%\"></i></div></div>";
  document.querySelector("#evidence-summary").innerHTML = "<div class=\"evidence-metrics\">"
    + evidenceRow(t("decision.traceability"), traceabilityPct + "%", traceabilityPct)
    + evidenceRow(t("decision.confidence"), confidencePct + "%", confidencePct)
    + evidenceRow(t("decision.sources"), t("decision.sourceCount", { count: sourceCount }), Math.min(100, sourceCount * 5))
    + "<small>" + escapeHtml(t("decision.evidenceNote")) + "</small></div>";
};

const updateMapPaint = () => {
  if (!map.getLayer("catchment-fill")) return;
  map.setPaintProperty("catchment-fill", "fill-color", ["case", ["get", "active"], "#0b9f92", "#3478d4"]);
  map.setPaintProperty("catchment-fill", "fill-opacity", ["case", ["get", "active"], currentTheme === "dark" ? 0.36 : 0.28, currentTheme === "dark" ? 0.13 : 0.08]);
  map.setPaintProperty("catchment-line", "line-color", ["case", ["get", "active"], "#0b9f92", "#3478d4"]);
};

const renderMap = (result, selectedCatchment) => {
  mapView = "site";
  for (const comparisonMarker of comparisonMarkers) comparisonMarker.remove();
  comparisonMarkers.length = 0;
  if (!map.isStyleLoaded()) {
    pendingMapRender = { result, selectedCatchment };
    return;
  }
  const catchment = result.catchments[selectedCatchment];
  const data = {
    type: "FeatureCollection",
    features: Object.entries(result.catchments).map(([name, item]) => ({
      type: "Feature",
      properties: { name, active: name === selectedCatchment },
      geometry: item.geometry
    }))
  };
  if (map.getSource("catchments")) {
    map.getSource("catchments").setData(data);
  } else {
    map.addSource("catchments", { type: "geojson", data });
    map.addLayer({ id: "catchment-fill", type: "fill", source: "catchments", paint: {} });
    map.addLayer({ id: "catchment-line", type: "line", source: "catchments", paint: { "line-width": ["case", ["get", "active"], 3, 1.5] } });
  }
  updateMapPaint();
  if (marker) marker.remove();
  marker = new maplibregl.Marker({ color: "#0b9f92" })
    .setLngLat([result.coordinates.longitude, result.coordinates.latitude])
    .addTo(map);
  const coordinatePairs = [];
  const collectPairs = (coordinates) => {
    if (Array.isArray(coordinates) && coordinates.length >= 2 && typeof coordinates[0] === "number") {
      coordinatePairs.push(coordinates);
      return;
    }
    if (Array.isArray(coordinates)) coordinates.forEach(collectPairs);
  };
  collectPairs(catchment.geometry.coordinates);
  if (coordinatePairs.length) {
    const bounds = coordinatePairs.reduce((accumulator, coordinate) => accumulator.extend(coordinate), new maplibregl.LngLatBounds());
    map.fitBounds(bounds, { padding: window.innerWidth <= 620 ? 28 : 55, maxZoom: 15 });
  }
};

function renderComparisonMapContext(payload) {
  document.querySelector("#map-context").textContent = t("map.comparison", {
    count: payload.sites.length, scope: catchmentLabel(payload.comparison_catchment)
  }) + (payload.data_mode === "SYNTHETIC_DEMO" ? " · " + t("map.synthetic") : "");
}

function renderComparisonMap(payload) {
  mapView = "comparison";
  pendingMapRender = null;
  if (marker) { marker.remove(); marker = null; }
  for (const comparisonMarker of comparisonMarkers) comparisonMarker.remove();
  comparisonMarkers.length = 0;
  // A previous single-site catchment must not appear to describe both sites.
  if (map.getSource("catchments")) map.getSource("catchments").setData({ type: "FeatureCollection", features: [] });
  const bounds = new maplibregl.LngLatBounds();
  for (const [index, site] of payload.sites.entries()) {
    const point = [site.coordinates.longitude, site.coordinates.latitude];
    const popup = new maplibregl.Popup().setText(`${index + 1}. ${site.standardized_address}`);
    comparisonMarkers.push(new maplibregl.Marker({ color: index ? "#805ad5" : "#0b9f92" })
      .setLngLat(point).setPopup(popup).addTo(map));
    bounds.extend(point);
  }
  map.fitBounds(bounds, { padding: 70, maxZoom: 14 });
  renderComparisonMapContext(payload);
}

function renderResult(result, selectedCatchment, updateMap = true) {
  if (!result.catchments[selectedCatchment]) selectedCatchment = Object.keys(result.catchments)[0];
  catchmentSelect.value = selectedCatchment;
  const catchment = result.catchments[selectedCatchment];
  const metrics = catchment.metrics;
  const distanceText = catchment.distance_m == null
    ? ""
    : Math.round(catchment.distance_m) + " m / " + (catchment.distance_m / 1609.344).toFixed(2) + " mi";
  const travelText = catchment.travel_minutes == null
    ? t("identity.radius", { distance: distanceText })
    : t("identity.walk", { minutes: catchment.travel_minutes, distance: distanceText });
  const areaSquareMiles = catchment.area_m2 / 2589988.110336;
  const identity = document.querySelector("#identity");
  identity.classList.remove("empty");
  identity.innerHTML = "<strong>" + escapeHtml(result.standardized_address) + "</strong><br>"
    + "BBL " + escapeHtml((result.parcel && result.parcel.bbl) || t("decision.unmatched"))
    + " · Census Tract " + escapeHtml(result.census_tract || "—")
    + " · " + escapeHtml(catchmentLabel(selectedCatchment))
    + " · " + escapeHtml(travelText)
    + " · " + (catchment.area_m2 / 1e6).toFixed(2) + " km² / " + areaSquareMiles.toFixed(2) + " mi²";

  const kpiIds = ["resident_population", "daytime_jobs", "direct_competitor_count", "audience_type"];
  const kpiIcons = { resident_population: "P", daytime_jobs: "J", direct_competitor_count: "C", audience_type: "A" };
  const kpis = document.querySelector("#kpis");
  kpis.classList.remove("empty-kpis");
  kpis.innerHTML = kpiIds.map((id) => {
    const metric = metrics[id];
    const detail = metric && (metric.source_year || metric.measure_type) || t("identity.snapshot");
    return "<div class=\"kpi\"><div class=\"kpi-top\"><span class=\"kpi-icon\">" + kpiIcons[id] + "</span><strong>"
      + escapeHtml(metricLabel(id)) + "</strong></div><div class=\"kpi-value\">" + escapeHtml(formatMetric(metric))
      + "</div><small>" + escapeHtml(detail) + "</small></div>";
  }).join("");

  document.querySelector("#demographics").innerHTML = metricList(metrics, [
    "resident_population_density_per_sq_mile", "median_household_income", "per_capita_income", "median_age",
    "age_under_25", "age_25_44", "age_45_64", "age_65_plus", "family_household_share",
    "nonfamily_household_share", "bachelors_or_higher_share", "civilian_labor_force_employment_rate",
    "civilian_labor_force_unemployment_rate", "foreign_born_share", "non_hispanic_white_share",
    "non_hispanic_black_share", "non_hispanic_asian_share", "hispanic_or_latino_share",
    "english_only_language_share", "spanish_language_share", "other_indo_european_language_share",
    "asian_pacific_language_share", "owner_occupied_share", "renter_occupied_share",
    "management_business_science_arts_occupation_share", "service_occupation_share", "sales_office_occupation_share"
  ]);
  document.querySelector("#audience").classList.remove("empty-copy");
  document.querySelector("#audience").innerHTML = "<p><span class=\"pill\">" + escapeHtml(formatMetric(metrics.audience_type)) + "</span></p>"
    + metricList(metrics, ["resident_index", "worker_index", "visitor_proxy_index", "nightlife_index", "transit_hub_index"]);
  document.querySelector("#competition").innerHTML = metricList(metrics, [
    "market_opportunity_profile", "demand_support_signal", "competition_pressure_signal", "direct_competitor_count",
    "substitute_count", "demand_complement_count", "closed_competitor_count",
    "competitors_per_1000_residents", "competitors_per_1000_jobs"
  ]);
  document.querySelector("#mobility").innerHTML = metricList(metrics, [
    "daytime_jobs", "top_workplace_industries", "regional_rail_station_count", "regional_transit_poi_count",
    "subway_complex_count", "subway_entrance_exit_count", "subway_entry_allowed_count", "subway_entries_2025",
    "bus_stop_count", "citibike_station_count", "citibike_bikes_available_current", "citibike_docks_available_current",
    "tlc_pickup_trips_average_daily_area_weighted", "tlc_dropoff_trips_average_daily_area_weighted",
    "pedestrian_count_latest_median", "traffic_speed_current_median_mph", "drove_alone_commute_share",
    "public_transit_commute_share", "walked_commute_share", "worked_from_home_share", "no_vehicle_household_share"
  ]);
  document.querySelector("#visitor-health").classList.remove("empty-copy");
  document.querySelector("#visitor-health").innerHTML = metricList(metrics, [
    "hotel_count", "regional_hotel_poi_count", "attraction_count", "nightlife_location_count",
    "regional_school_poi_count", "regional_healthcare_poi_count", "regional_park_poi_count",
    "regional_library_poi_count", "pm25_indicator", "asthma_health_indicator",
    "cdc_places_current_asthma_pct", "cdc_places_diagnosed_diabetes_pct", "cdc_places_obesity_pct",
    "cdc_places_uninsured_pct", "cdc_places_food_insecurity_pct", "environmental_exposure_profile",
    "fema_special_flood_hazard_overlap_pct", "hurricane_evacuation_overlap_pct",
    "future_2050_floodplain_overlap_pct", "sea_level_2100_floodplain_overlap_pct",
    "regulatory_risk_signal", "active_dob_violation_count", "active_dob_violations_per_100_parcels",
    "active_ecb_violation_count", "active_ecb_violations_per_100_parcels"
  ]);
  document.querySelector("#real-estate").innerHTML = metricList(metrics, [
    "parcel_count", "building_floor_area_sqft", "residential_floor_area_sqft", "office_floor_area_sqft",
    "retail_floor_area_sqft", "rolling_sale_record_count", "rolling_sale_price_median",
    "historical_transaction_case_count", "historical_transaction_amount_median",
    "acris_deed_document_count_24m", "building_permit_issued_24m_count",
    "active_dob_violation_count", "active_ecb_violation_count", "regulatory_risk_signal"
  ]);

  const uniqueSources = new Map();
  Object.entries(metrics).forEach(([id, metric]) => {
    if (!metric.source_id || metric.source_id === "derived") return;
    const key = metric.source_id + "|" + (metric.snapshot_id || "");
    if (!uniqueSources.has(key)) uniqueSources.set(key, [id, metric]);
  });
  const sourceRows = [...uniqueSources.values()].map(([id, metric]) => (
    "<li><span>" + escapeHtml(metricLabel(id)) + "</span><b>" + escapeHtml(metric.source_id)
    + "</b><small>" + escapeHtml(metric.source_year || "") + " · " + escapeHtml(metric.measure_type || "")
    + " · " + escapeHtml(metric.snapshot_id || "") + "</small></li>"
  )).join("");
  document.querySelector("#sources").innerHTML = "<ul class=\"metric-list\">" + (sourceRows || "<li>" + escapeHtml(t("metric.noData")) + "</li>") + "</ul>";

  if (updateMap) {
    document.querySelector("#map-context").textContent = catchmentLabel(selectedCatchment) + " · " + result.standardized_address;
    renderMap(result, selectedCatchment);
  }
  renderDecisionSummary(result, selectedCatchment, metrics);
  renderExports();
}

function renderComparison(payload) {
  const metricIds = [
    "resident_population", "daytime_jobs", "direct_competitor_count", "regional_rail_station_count",
    "regional_transit_poi_count", "median_household_income", "historical_transaction_case_count"
  ].filter((id) => payload.metric_comparisons[id]);
  const valuesByMetricAndSite = new Map();
  metricIds.forEach((id) => {
    payload.metric_comparisons[id].values.forEach((item) => valuesByMetricAndSite.set(id + "|" + item.site_id, item));
  });
  const header = metricIds.map((id) => "<th>" + escapeHtml(metricLabel(id)) + "</th>").join("");
  const body = payload.sites.map((site) => {
    const cells = metricIds.map((id) => {
      const item = valuesByMetricAndSite.get(id + "|" + site.site_id);
      const rank = item && item.high_to_low_rank != null ? "<small>" + escapeHtml(t("comparison.rank", { rank: item.high_to_low_rank })) + "</small>" : "";
      return "<td><b>" + escapeHtml(formatMetric(item)) + "</b>" + rank + "</td>";
    }).join("");
    return "<tr><td><strong>" + escapeHtml(site.standardized_address) + "</strong><br><small>"
      + escapeHtml(site.state_abbr || "") + " · County " + escapeHtml(site.county_geoid || "—")
      + " · " + escapeHtml(t("comparison.missing", { count: site.missing_data.length })) + "</small></td>" + cells + "</tr>";
  }).join("");
  const result = document.querySelector("#comparison-result");
  result.classList.remove("empty");
  const translatedWarnings = {
    "Rows are raw same-metric comparisons. High-to-low rank does not mean better, recommended, causal, or suitable.": "各行仅比较同一指标的原始值；高低排序不代表更优、推荐、因果关系或适用性。",
    "No composite site score is produced because NYC Lens has no approved real-project calibration set.": "由于缺少经批准的真实项目校准集，NYC Lens 不生成综合选址评分。",
    "Mixed jurisdictions can have different source coverage; each value retains its source, year, confidence, missingness and warnings.": "不同辖区的数据覆盖可能不同；每个值均保留来源、年份、置信信息、缺失项和警示。",
    "Within-batch percentiles describe only this small submitted address set and are not citywide or regional benchmarks.": "批内百分位只描述本次提交的少量地址，不是全市或区域基准。"
  };
  const warning = payload.warnings?.length
    ? payload.warnings.map(value => currentLanguage === "zh" ? translatedWarnings[value] || value : value).join(" ")
    : t("comparison.warning");
  result.innerHTML = "<table class=\"comparison-table\"><thead><tr><th>" + escapeHtml(t("comparison.address"))
    + "</th>" + header + "</tr></thead><tbody>" + body + "</tbody></table><p class=\"map-note\">" + escapeHtml(warning) + "</p>";
  const exports = document.querySelector("#comparison-exports");
  exports.hidden = false;
  exports.innerHTML = ["json", "csv", "xlsx"].map((format) => (
    "<a href=\"/api/comparison/" + encodeURIComponent(payload.comparison_id) + "/report?format=" + format + "\">" + format.toUpperCase() + "</a>"
  )).join("");
  if (typeof renderAssistantBrief === "function") renderAssistantBrief(payload);
}

document.querySelectorAll(".tab-button").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab-button").forEach((buttonElement) => {
      const active = buttonElement === tab;
      buttonElement.classList.toggle("active", active);
      buttonElement.setAttribute("aria-selected", String(active));
    });
    document.querySelectorAll("[data-tab-panel]").forEach((panel) => {
      const active = panel.dataset.tabPanel === tab.dataset.panel;
      panel.classList.toggle("active", active);
      panel.hidden = !active;
    });
  });
  tab.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    const tabs = [...document.querySelectorAll(".tab-button")];
    const direction = event.key === "ArrowRight" ? 1 : -1;
    const next = tabs[(tabs.indexOf(tab) + direction + tabs.length) % tabs.length];
    next.focus();
    next.click();
  });
});

document.querySelectorAll("input[name=catchment]").forEach((checkbox) => {
  checkbox.addEventListener("change", () => {
    const previous = catchmentSelect.value;
    syncCatchmentControls(previous);
    if (!selectedCatchments().length) {
      setStatus("status.noCatchment", {}, "error");
      return;
    }
    if (currentResult && currentResult.catchments[catchmentSelect.value]) {
      renderResult(currentResult, catchmentSelect.value);
    } else if (currentResult) {
      setStatus("status.settingsChanged", {}, "ready");
    }
  });
});

document.querySelector("#reset-filters").addEventListener("click", () => {
  const defaults = new Set(["500m", "1km", "10min_walk"]);
  document.querySelectorAll("input[name=catchment]").forEach((checkbox) => {
    checkbox.checked = defaults.has(checkbox.value);
  });
  syncCatchmentControls("10min_walk");
  if (currentResult && currentResult.catchments["10min_walk"]) renderResult(currentResult, "10min_walk");
  else if (currentResult) setStatus("status.settingsChanged", {}, "ready");
});

catchmentSelect.addEventListener("change", () => {
  if (currentResult && currentResult.catchments[catchmentSelect.value]) renderResult(currentResult, catchmentSelect.value);
  else if (currentResult) setStatus("status.settingsChanged", {}, "ready");
});

themeToggle.addEventListener("click", () => {
  currentTheme = currentTheme === "dark" ? "light" : "dark";
  safeStorageSet("nyc-lens-theme", currentTheme);
  applyTheme();
});

languageToggle.addEventListener("click", () => {
  currentLanguage = currentLanguage === "zh" ? "en" : "zh";
  safeStorageSet("nyc-lens-language", currentLanguage);
  applyLanguage();
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const catchments = selectedCatchments();
  if (!catchments.length) {
    setStatus("status.noCatchment", {}, "error");
    return;
  }
  button.disabled = true;
  setStatus("status.analyzing", {}, "loading");
  try {
    const response = await fetch("/api/site-analysis", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        address: document.querySelector("#address").value,
        business_type: document.querySelector("#business-type").value,
        catchments
      })
    });
    const payload = await readApiPayload(response);
    if (!response.ok) throw new Error(apiErrorMessage(payload, t("status.failure")));
    currentResult = payload;
    const available = Object.keys(payload.catchments);
    const preferred = available.includes(catchmentSelect.value)
      ? catchmentSelect.value
      : available.includes("10min_walk") ? "10min_walk" : available[0];
    syncCatchmentControls(preferred, available);
    renderResult(payload, preferred);
    setStatus("status.success", { count: payload.missing_data.length }, "ready");
  } catch (error) {
    setStatus("status.failure", {}, "error", error.message);
  } finally {
    button.disabled = false;
  }
});

comparisonForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const addresses = document.querySelector("#comparison-addresses").value
    .split(/\r?\n/)
    .map((value) => value.trim())
    .filter(Boolean);
  if (addresses.length < 2 || addresses.length > 20) {
    setComparisonStatus("comparison.invalidCount", {}, "error");
    return;
  }
  const comparisonCatchment = document.querySelector("#comparison-catchment").value;
  compareButton.disabled = true;
  setComparisonStatus("comparison.loading", {
    scope: catchmentLabel(comparisonCatchment),
    count: addresses.length
  }, "loading");
  try {
    const response = await fetch("/api/site-comparison", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        addresses,
        business_type: document.querySelector("#business-type").value,
        catchments: [comparisonCatchment],
        comparison_catchment: comparisonCatchment
      })
    });
    const payload = await readApiPayload(response);
    if (!response.ok) throw new Error(apiErrorMessage(payload, t("comparison.failure")));
    currentComparison = payload;
    renderComparison(payload);
    renderComparisonMap(payload);
    setComparisonStatus("comparison.success", {
      sites: payload.sites.length,
      metrics: Object.keys(payload.metric_comparisons).length
    }, "ready");
  } catch (error) {
    setComparisonStatus("comparison.failure", {}, "error", error.message);
  } finally {
    compareButton.disabled = false;
  }
});

syncCatchmentControls("10min_walk");
applyLanguage();
