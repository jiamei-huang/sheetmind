const LEGACY_SECTION_HEADING = /(^|[ \t]+)\*\*([^*\n]{1,48}[：:])\*\*[ \t]*/gm;
const LEGACY_INSIGHT_MARKER = /(^|[ \t]+)(?=(?:🟢|🔴|📊|📈|📉|💡|⚠️|✅)\s*)/gmu;
const PLAIN_SECTION_HEADING = /(^|\n+|[。！？][ \t]+)(关键结论|关键发现|数据模式|业务建议|风险提示|下一步建议|分析结论|结论)[：:][ \t]*/gm;
const CHINESE_LIST_START = /(^|\n\n)(?=(?:第一[，,:：]|一是))/g;
const CHINESE_LIST_CONTINUATION = /；[ \t]*(?=(?:第二[，,:：]|第三[，,:：]|第四[，,:：]|第五[，,:：]|二是|三是|四是|五是))/g;

export const normalizeAnalysisMarkdown = (value) => {
  if (!value) return "";

  return String(value)
    .trim()
    .replace(
      LEGACY_SECTION_HEADING,
      (_, prefix, heading) => `${prefix ? "\n\n" : ""}### ${heading}\n\n`
    )
    .replace(
      LEGACY_INSIGHT_MARKER,
      (prefix) => `${prefix ? "\n\n" : ""}- `
    )
    .replace(
      PLAIN_SECTION_HEADING,
      (_, prefix, heading) => `${prefix.trimEnd()}${prefix ? "\n\n" : ""}### ${heading}\n\n`
    )
    .replace(CHINESE_LIST_START, "$1- ")
    .replace(CHINESE_LIST_CONTINUATION, "\n- ")
    .replace(/\n{3,}/g, "\n\n");
};
