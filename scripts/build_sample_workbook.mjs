import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const [inputPath, outputPath, previewDir, sourceRegistryPath] = process.argv.slice(2);
if (!inputPath || !outputPath || !previewDir || !sourceRegistryPath) {
  throw new Error("Usage: node build_sample_workbook.mjs input.json output.xlsx preview_dir source_registry.json");
}

const result = JSON.parse(await fs.readFile(inputPath, "utf8"));
const registry = JSON.parse(await fs.readFile(sourceRegistryPath, "utf8"));
const sourceUrls = registry.sources || registry;
const metricLabels = registry.metrics || {};
const workbook = Workbook.create();
const summary = workbook.worksheets.add("概览");
const comparison = workbook.worksheets.add("商圈对比");
const detail = workbook.worksheets.add("指标明细");
const sources = workbook.worksheets.add("来源与限制");
workbook.comments.setSelf({ displayName: "NYC Lens" });

const colors = {
  navy: "#16324F",
  teal: "#18A999",
  paleTeal: "#EAF4F2",
  paleBlue: "#EDF3F8",
  amber: "#F4B942",
  paleAmber: "#FFF4D6",
  ink: "#1F2933",
  muted: "#5F6F7F",
  white: "#FFFFFF",
  border: "#D7E0E7",
};

const clean = (value) => {
  if (value === undefined || value === null) return null;
  if (typeof value === "number" && !Number.isFinite(value)) return null;
  return value;
};

const metricRows = [];
const detailRowByKey = new Map();
for (const [catchmentName, catchment] of Object.entries(result.catchments)) {
  for (const [metricId, metric] of Object.entries(catchment.metrics)) {
    metricRows.push([
      catchmentName,
      metricId,
      metricLabels[metricId] || metricId,
      clean(metric.value),
      clean(metric.text_value),
      clean(metric.unit),
      clean(metric.source_id),
      clean(metric.snapshot_id),
      clean(metric.source_year),
      clean(metric.measure_type),
      clean(metric.confidence),
      clean(metric.margin_of_error),
      clean(metric.geography_type),
      clean(metric.geography_id),
      clean(metric.percentile),
      (metric.warnings || []).join(" | "),
    ]);
  }
}

const detailHeaders = [
  "商圈", "metric_id", "中文指标", "数值", "文本值", "单位", "source_id", "snapshot_id",
  "年份/期间", "事实类型", "置信度", "MOE", "地理粒度", "geography_id", "NYC同口径百分位", "限制与说明",
];
detail.getRangeByIndexes(0, 0, metricRows.length + 1, detailHeaders.length).values = [detailHeaders, ...metricRows];
metricRows.forEach((row, index) => detailRowByKey.set(`${row[0]}|${row[1]}`, index + 2));
detail.showGridLines = false;
detail.freezePanes.freezeRows(1);
detail.getRange(`A1:P1`).format = {
  fill: colors.navy,
  font: { bold: true, color: colors.white },
  wrapText: true,
  borders: { preset: "all", style: "thin", color: colors.border },
};
detail.getRange(`A2:P${metricRows.length + 1}`).format = {
  font: { color: colors.ink },
  verticalAlignment: "top",
  borders: { preset: "all", style: "thin", color: colors.border },
};
detail.getRange(`K2:K${metricRows.length + 1}`).format.numberFormat = "0%";
detail.getRange(`D2:D${metricRows.length + 1}`).format.numberFormat = "#,##0.00";
detail.getRange(`L2:L${metricRows.length + 1}`).format.numberFormat = "#,##0.00";
detail.getRange(`O2:O${metricRows.length + 1}`).format.numberFormat = "0.0";
detail.getRange(`P2:P${metricRows.length + 1}`).format.wrapText = true;
detail.getRange(`K2:K${metricRows.length + 1}`).conditionalFormats.add("colorScale", {
  colors: ["#FECACA", "#FEF3C7", "#BBF7D0"],
  thresholds: ["min", "50%", "max"],
});
const detailWidths = [15, 32, 28, 14, 22, 22, 24, 38, 24, 30, 12, 14, 28, 26, 18, 64];
detailWidths.forEach((width, index) => detail.getRangeByIndexes(0, index, 1, 1).format.columnWidth = width);
detail.getRange(`A1:P${metricRows.length + 1}`).format.autofitRows();

const formulaFor = (catchment, metricId, column = "D") => {
  const row = detailRowByKey.get(`${catchment}|${metricId}`);
  return row ? `='指标明细'!${column}${row}` : '=""';
};

summary.showGridLines = false;
summary.mergeCells("A1:H2");
summary.getRange("A1:H2").values = [["NYC Lens｜商圈分析概览"]];
summary.getRange("A1:H2").format = {
  fill: colors.navy,
  font: { bold: true, color: colors.white, size: 20 },
  verticalAlignment: "center",
};
summary.getRange("A4:B10").values = [
  ["标准地址", result.standardized_address],
  ["坐标", `${result.coordinates.latitude.toFixed(6)}, ${result.coordinates.longitude.toFixed(6)}`],
  ["行政区", result.borough],
  ["BBL", result.parcel?.bbl || "未匹配"],
  ["Census Tract", result.census_tract],
  ["H3 Resolution 10", result.h3_resolution_10],
  ["计算版本", result.calculation_version_id],
];
summary.getRange("A4:A10").format = { fill: colors.paleBlue, font: { bold: true, color: colors.navy } };
summary.getRange("A4:B10").format.borders = { preset: "all", style: "thin", color: colors.border };

const cards = [
  ["D4:E4", "D5:E6", "常住人口｜10分钟步行", formulaFor("10min_walk", "resident_population")],
  ["F4:G4", "F5:G6", "白天岗位｜10分钟步行", formulaFor("10min_walk", "daytime_jobs")],
  ["D8:E8", "D9:E10", "餐饮竞争者｜10分钟步行", formulaFor("10min_walk", "direct_competitor_count")],
  ["F8:G8", "F9:G10", "家庭收入中位数", formulaFor("10min_walk", "median_household_income")],
  ["D12:E12", "D13:E14", "地铁站复合体", formulaFor("10min_walk", "subway_complex_count")],
  ["F12:G12", "F13:G14", "客群类型", formulaFor("10min_walk", "audience_type", "E")],
];
for (const [labelRange, valueRange, label, formula] of cards) {
  summary.mergeCells(labelRange);
  summary.mergeCells(valueRange);
  summary.getRange(labelRange).values = [[label]];
  summary.getRange(valueRange).formulas = [[formula]];
  summary.getRange(labelRange).format = {
    fill: colors.teal,
    font: { bold: true, color: colors.white },
    horizontalAlignment: "center",
    verticalAlignment: "center",
  };
  summary.getRange(valueRange).format = {
    fill: colors.paleTeal,
    font: { bold: true, color: colors.navy, size: 18 },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    borders: { preset: "outside", style: "thin", color: colors.teal },
  };
}
summary.getRange("D5:G13").format.numberFormat = "#,##0";
summary.getRange("F9:G10").format.numberFormat = "$#,##0";
summary.mergeCells("A17:H20");
summary.getRange("A17:H20").values = [[
  "重要说明：ACS 为区域调查估计并保留 Margin of Error；LODES 统计岗位而非独立自然人；游客与客群为未校准开放数据代理。健康、收入、语言与族裔信息仅描述公开地理范围，不代表具体个人、家庭或建筑。环境图层只用于商圈级筛查，不是保险、工程或法律结论。",
]];
summary.getRange("A17:H20").format = {
  fill: colors.paleAmber,
  font: { color: colors.ink, size: 10 },
  wrapText: true,
  verticalAlignment: "top",
  borders: { preset: "outside", style: "thin", color: colors.amber },
};
summary.getRange("A22:B23").values = [
  ["生成自 site_id", result.site_id],
  ["缺失数据项", result.missing_data.length],
];
summary.getRange("A22:A23").format.font = { bold: true, color: colors.navy };
summary.getRange("A1:H23").format.rowHeight = 22;
summary.getRange("A1:H2").format.rowHeight = 34;
summary.getRange("A17:H20").format.rowHeight = 28;
[18, 42, 4, 22, 22, 22, 22, 4].forEach((width, index) => summary.getRangeByIndexes(0, index, 1, 1).format.columnWidth = width);

comparison.showGridLines = false;
comparison.mergeCells("A1:M1");
comparison.getRange("A1:M1").values = [["六种商圈对比｜所有数值链接到“指标明细”"]];
comparison.getRange("A1:M1").format = {
  fill: colors.navy,
  font: { bold: true, color: colors.white, size: 16 },
  verticalAlignment: "center",
};
comparison.getRange("A1:M1").format.rowHeight = 32;
const comparisonHeaders = ["商圈", "面积 km²", "常住人口", "家庭收入中位数", "年龄中位数", "白天岗位", "竞争者", "替代品", "地铁复合体", "酒店", "游客代理指数", "机会画像", "环境暴露画像"];
comparison.getRange("A3:M3").values = [comparisonHeaders];
comparison.getRange("A3:M3").format = { fill: colors.teal, font: { bold: true, color: colors.white }, wrapText: true };
const catchmentNames = Object.keys(result.catchments);
for (let index = 0; index < catchmentNames.length; index += 1) {
  const catchment = catchmentNames[index];
  const row = index + 4;
  comparison.getRange(`A${row}`).values = [[catchment]];
  comparison.getRange(`B${row}`).values = [[result.catchments[catchment].area_m2 / 1_000_000]];
  const metrics = [
    "resident_population", "median_household_income", "median_age", "daytime_jobs",
    "direct_competitor_count", "substitute_count", "subway_complex_count", "hotel_count", "visitor_proxy_index",
  ];
  comparison.getRange(`C${row}:K${row}`).formulas = [[...metrics.map((metric) => formulaFor(catchment, metric))]];
  comparison.getRange(`L${row}:M${row}`).formulas = [[
    formulaFor(catchment, "market_opportunity_profile", "E"),
    formulaFor(catchment, "environmental_exposure_profile", "E"),
  ]];
}
comparison.getRange(`A3:M${catchmentNames.length + 3}`).format.borders = { preset: "all", style: "thin", color: colors.border };
comparison.getRange(`B4:B${catchmentNames.length + 3}`).format.numberFormat = "0.00";
comparison.getRange(`C4:C${catchmentNames.length + 3}`).format.numberFormat = "#,##0";
comparison.getRange(`D4:D${catchmentNames.length + 3}`).format.numberFormat = "$#,##0";
comparison.getRange(`E4:K${catchmentNames.length + 3}`).format.numberFormat = "#,##0.0";
comparison.freezePanes.freezeRows(3);
[17, 14, 16, 22, 16, 16, 14, 14, 16, 12, 18, 26, 28].forEach((width, index) => comparison.getRangeByIndexes(0, index, 1, 1).format.columnWidth = width);
comparison.getRange("A12:M14").merge();
comparison.getRange("A12:M14").values = [[
  "步行商圈由 OSM 路网计算；网络阈值分别为 400m、800m、1200m。径向商圈使用 EPSG:32118 投影缓冲。不同大小商圈的原始计数不能当作同口径排名。",
]];
comparison.getRange("A12:M14").format = {
  fill: colors.paleBlue,
  wrapText: true,
  font: { color: colors.muted },
  verticalAlignment: "center",
  rowHeight: 28,
};

const sourceMap = new Map();
for (const row of metricRows) {
  const key = `${row[6]}|${row[7]}|${row[8]}|${row[9]}`;
  if (!sourceMap.has(key)) sourceMap.set(key, [row[6], row[7], row[8], row[9], row[15]]);
}
const sourceHeaders = ["source_id", "snapshot_id", "年份/期间", "事实类型", "来源/方法 URL", "限制"];
const sourceRows = [...sourceMap.values()].map((row) => [
  row[0], row[1], row[2], row[3],
  sourceUrls[row[0]] || (row[0] === "derived_citywide_benchmark" ? "https://h3geo.org/" : "本地派生；见 docs/methodology.md"),
  row[4],
]);
sources.getRangeByIndexes(0, 0, sourceRows.length + 1, sourceHeaders.length).values = [sourceHeaders, ...sourceRows];
sources.showGridLines = false;
sources.freezePanes.freezeRows(1);
sources.getRange("A1:F1").format = { fill: colors.navy, font: { bold: true, color: colors.white }, wrapText: true };
sources.getRange(`A2:F${sourceRows.length + 1}`).format = {
  borders: { preset: "all", style: "thin", color: colors.border },
  verticalAlignment: "top",
  wrapText: true,
};
[25, 40, 26, 32, 62, 72].forEach((width, index) => sources.getRangeByIndexes(0, index, 1, 1).format.columnWidth = width);
sources.getRange(`A1:F${sourceRows.length + 1}`).format.autofitRows();

workbook.comments.addThread(
  { cell: detail.getRange("K1") },
  "置信度是 NYC Lens 对输入与方法的保守分档，不是统计显著性或预测准确率。",
);
workbook.comments.addThread(
  { cell: sources.getRange("A1") },
  "完整来源台账位于 config/sources.yml；每个 Raw 快照另有 manifest 与 SHA-256。",
);

await fs.mkdir(path.dirname(outputPath), { recursive: true });
await fs.mkdir(previewDir, { recursive: true });

const inspection = await workbook.inspect({
  kind: "workbook,sheet,formula",
  maxChars: 8000,
  tableMaxRows: 8,
  tableMaxCols: 16,
  options: { maxResults: 100 },
});
console.log("INSPECTION\n" + inspection.ndjson);
const summaryCheck = await workbook.inspect({
  kind: "table",
  range: "概览!A1:H23",
  include: "values,formulas",
  tableMaxRows: 23,
  tableMaxCols: 8,
  maxChars: 6000,
});
console.log("SUMMARY_CHECK\n" + summaryCheck.ndjson);
const errorScan = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 100 },
  maxChars: 4000,
});
console.log("ERROR_SCAN\n" + errorScan.ndjson);

const previewRanges = {
  概览: "A1:H23",
  商圈对比: "A1:M14",
  指标明细: "A1:P25",
  来源与限制: "A1:F30",
};
for (const sheetName of ["概览", "商圈对比", "指标明细", "来源与限制"]) {
  const preview = await workbook.render({ sheetName, range: previewRanges[sheetName], scale: 1.2, format: "png" });
  await fs.writeFile(path.join(previewDir, `${sheetName}.png`), new Uint8Array(await preview.arrayBuffer()));
}

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
console.log(JSON.stringify({ outputPath, previewDir, sheets: ["概览", "商圈对比", "指标明细", "来源与限制"], rows: metricRows.length }));
