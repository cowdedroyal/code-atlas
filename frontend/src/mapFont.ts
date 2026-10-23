import type { TextLayerProps } from "@deck.gl/layers";

// deck.gl 9.4 subtracts the left bearing from the right bound and draws at the
// unadjusted origin. Keep ink bounds separate from advance to preserve j's hook.
export const mapFontRenderer: NonNullable<TextLayerProps["_getFontRenderer"]> = (settings) => {
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d", { willReadFrequently: true })!;
  const style = () => {
    ctx.font = `${settings.fontWeight} ${settings.fontSize}px ${settings.fontFamily}`;
    ctx.textBaseline = "alphabetic";
    ctx.textAlign = "left";
  };
  style();
  const bounds = (char: string) => {
    const m = ctx.measureText(char);
    const left = Math.floor(-m.actualBoundingBoxLeft), right = Math.ceil(m.actualBoundingBoxRight);
    return { left, width: Math.max(0, right - left), ascent: Math.ceil(m.actualBoundingBoxAscent),
      descent: Math.ceil(m.actualBoundingBoxDescent), advance: m.width };
  };
  return {
    measure: (char) => {
      if (char !== undefined) return bounds(char);
      const m = ctx.measureText("M");
      return { advance: 0, width: 0, ascent: Math.ceil(m.fontBoundingBoxAscent || settings.fontSize * 0.8),
        descent: Math.ceil(m.fontBoundingBoxDescent || settings.fontSize * 0.2) };
    },
    draw: (char) => {
      const b = bounds(char);
      canvas.width = Math.max(1, b.width);
      canvas.height = Math.max(1, b.ascent + b.descent);
      style();
      ctx.fillText(char, -b.left, b.ascent);
      return { data: ctx.getImageData(0, 0, canvas.width, canvas.height), left: -b.left };
    },
  };
};

export const MAP_FONT = { fontSettings: { fontSize: 64, buffer: 12 }, _getFontRenderer: mapFontRenderer };
