/** S18: one set of upload rules for the Field Screener and the admin upload (the server re-checks every file with
 *  the same decoder and limits, app/pipeline/acquisition.py; these only fail fast in the browser). */
export const TYPES = ["image/jpeg", "image/png", "image/webp", "image/tiff", "image/bmp", "image/gif", "image/heic", "image/heif"];
export const EXT = /\.(jpe?g|png|webp|tiff?|bmp|gif|heic|heif)$/i;
export const HEIC = /\.(heic|heif)$/i;
export const ACCEPT = "image/jpeg,image/heic,image/heif,.heic,.heif,image/png,image/webp,image/tiff,image/bmp,image/gif";
export const DEFAULT_MAX_MB = 64; // Settings.max_upload_mb (app/core/config.py)
export const fmtSize = (b) => (b >= 2 ** 20 ? `${(b / 2 ** 20).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1024))} KB`);
/** HEIC from a phone often arrives with an empty MIME type: the extension decides. */
export const isPhoto = (f) => TYPES.includes(f.type) || EXT.test(f.name);
export function checkPhoto(f, maxMb = DEFAULT_MAX_MB) {
  if (!isPhoto(f)) return `“${f.name}” is not a supported image. Use a JPEG, HEIC, PNG, WebP, TIFF, BMP or GIF photo.`;
  if (f.size > maxMb * 2 ** 20) return `“${f.name}” is ${fmtSize(f.size)}; the limit is ${maxMb} MB.`;
  return null;
}
