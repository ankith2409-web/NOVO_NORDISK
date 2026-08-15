/**
 * The icons this interface uses, drawn rather than imported.
 *
 * Two rules behind that. Emoji are not icons: they render as somebody else's
 * artwork at somebody else's baseline, they change between platforms, and a
 * screen reader announces "sparkles" where a button meant "generate". A "✨"
 * on the summary button was doing exactly that until this file existed.
 *
 * And no icon package. The whole app is inlined into one file served without a
 * CDN; pulling in a library to draw six glyphs would cost more than the six
 * glyphs. These are Lucide's geometry at 24×24, stroked rather than filled so
 * they sit correctly beside text at any weight.
 *
 * Every icon is `aria-hidden`: an icon in this interface is always beside a
 * word or inside a button that carries its own accessible name, so announcing
 * it a second time is noise.
 */
import type { SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement> & { size?: number };

function Glyph({ size = 16, children, ...rest }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      {children}
    </svg>
  );
}

/** Send a message. Points the way the message travels. */
export function SendIcon(props: IconProps) {
  return (
    <Glyph {...props}>
      <path d="M22 2 11 13" />
      <path d="M22 2 15 22l-4-9-9-4Z" />
    </Glyph>
  );
}

export function CloseIcon(props: IconProps) {
  return (
    <Glyph {...props}>
      <path d="M18 6 6 18" />
      <path d="m6 6 12 12" />
    </Glyph>
  );
}

/** Generated content. The one place this interface signals "a model wrote this". */
export function SparkIcon(props: IconProps) {
  return (
    <Glyph {...props}>
      <path d="M12 3v4M12 17v4M3 12h4M17 12h4" />
      <path d="M12 8.5 13.2 11 15.5 12l-2.3 1-1.2 2.5L10.8 13 8.5 12l2.3-1Z" />
    </Glyph>
  );
}

export function AlertIcon(props: IconProps) {
  return (
    <Glyph {...props}>
      <path d="M12 9v4" />
      <path d="M12 17h.01" />
      <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" />
    </Glyph>
  );
}

export function RetryIcon(props: IconProps) {
  return (
    <Glyph {...props}>
      <path d="M3 12a9 9 0 1 0 3-6.7L3 8" />
      <path d="M3 3v5h5" />
    </Glyph>
  );
}

export function ChevronIcon(props: IconProps) {
  return (
    <Glyph {...props}>
      <path d="m9 18 6-6-6-6" />
    </Glyph>
  );
}
