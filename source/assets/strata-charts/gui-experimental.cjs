"use strict";

const { CodeBlock, Plugin } = require("@opencode/plugin/gui");

function parseChart(code) {
  if (code.length > 24000) return;
  let spec;
  try {
    spec = JSON.parse(code);
  } catch {
    return;
  }
  const text = (value, limit) =>
    typeof value === "string" && value.trim().length > 0 && value.length <= limit;
  if (!spec || !text(spec.title, 120)) return;
  const { categories, series } = spec;
  if (!Array.isArray(categories) || categories.length < 1 || categories.length > 24) return;
  if (!categories.every((label) => text(label, 80))) return;
  if (!Array.isArray(series) || series.length < 1 || series.length > 6) return;
  if (!series.every((item) =>
    item && text(item.label, 60) &&
    (item.unit === undefined || typeof item.unit === "string" && item.unit.length <= 30) &&
    Array.isArray(item.values) && item.values.length === categories.length &&
    item.values.every((value) => Number.isFinite(value) && Math.abs(value) <= 1e100)
  )) return;
  return spec;
}

function element(tag, label, style) {
  const node = document.createElement(tag);
  if (label !== undefined) node.textContent = label;
  if (style) Object.assign(node.style, style);
  return node;
}

function chart(spec) {
  const root = element("section", undefined, {
    border: "1px solid color-mix(in srgb, currentColor 18%, transparent)",
    borderRadius: "10px",
    padding: "12px",
    margin: "6px 0",
    minWidth: "0",
    fontFamily: "inherit",
  });
  root.setAttribute("aria-label", spec.title);
  root.append(element("div", spec.title, {
    fontSize: "14px", fontWeight: "600", overflowWrap: "anywhere",
  }));
  const choices = element("div", undefined, {
    display: "flex", flexWrap: "wrap", gap: "6px", marginTop: "8px",
  });
  choices.setAttribute("role", "group");
  choices.setAttribute("aria-label", "Chart metric");
  const rows = element("div", undefined, { display: "grid", gap: "7px", marginTop: "9px" });
  rows.setAttribute("aria-live", "polite");
  if (spec.series.length > 1) root.append(choices);
  root.append(rows);
  const number = new Intl.NumberFormat(undefined, { maximumSignificantDigits: 8 });
  const buttons = [];

  function draw(index) {
    const { label, unit, values } = spec.series[index];
    const min = Math.min(0, ...values);
    const max = Math.max(0, ...values);
    const range = max - min || 1;
    rows.replaceChildren();
    rows.setAttribute("aria-label", label);
    rows.append(element("div", unit ? `${label} (${unit})` : label, { fontSize: "12px" }));
    spec.categories.forEach((category, rowIndex) => {
      const value = values[rowIndex];
      const valueText = unit ? `${number.format(value)} ${unit}` : number.format(value);
      const row = element("div", undefined, {
        display: "grid", gap: "3px", fontSize: "12px", minWidth: "0",
      });
      const caption = element("div", undefined, {
        display: "flex", flexWrap: "wrap", justifyContent: "space-between", gap: "1px 10px",
        overflowWrap: "anywhere",
      });
      caption.append(element("span", category), element("span", valueText, {
        fontVariantNumeric: "tabular-nums",
      }));
      const track = element("div", undefined, {
        position: "relative", height: "9px", borderRadius: "4px", overflow: "hidden",
        background: "color-mix(in srgb, currentColor 9%, transparent)",
      });
      track.setAttribute("aria-hidden", "true");
      track.title = `${category} · ${valueText}`;
      const bar = element("div", undefined, {
        position: "absolute", top: "0", bottom: "0", borderRadius: "4px",
        left: `${100 * (Math.min(0, value) - min) / range}%`,
        width: `${100 * Math.abs(value) / range}%`,
        background: "color-mix(in srgb, #7c65db 76%, currentColor)",
      });
      track.append(bar);
      row.append(caption, track);
      rows.append(row);
    });
    buttons.forEach((button, buttonIndex) => {
      const selected = buttonIndex === index;
      button.setAttribute("aria-pressed", String(selected));
      button.style.background = selected
        ? "color-mix(in srgb, currentColor 15%, transparent)" : "transparent";
    });
  }

  if (spec.series.length > 1) spec.series.forEach((series, index) => {
    const button = element("button", series.label, {
      border: "1px solid color-mix(in srgb, currentColor 20%, transparent)",
      borderRadius: "6px", padding: "4px 8px", color: "inherit", font: "inherit",
      fontSize: "12px", maxWidth: "100%", overflowWrap: "anywhere", cursor: "pointer",
    });
    button.type = "button";
    button.addEventListener("click", () => draw(index));
    buttons.push(button);
    choices.append(button);
  });
  draw(0);
  return root;
}

module.exports = {
  default: Plugin.define({
    setup(ctx) {
      ctx.add(CodeBlock, {
        languages: ["strata-chart"],
        render({ code }) {
          const spec = parseChart(code);
          if (spec) return chart(spec);
        },
      });
    },
  }),
};
