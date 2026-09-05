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

/** Arrow into a tray: the download convention, drawn to match the set. */
export function DownloadIcon(props: IconProps) {
  return (
    <Glyph {...props}>
      <path d="M12 3v12" />
      <path d="m7 11 5 5 5-5" />
      <path d="M4 20h16" />
    </Glyph>
  );
}

/** Arrow out of a tray: the download glyph inverted, as the convention runs. */
export function UploadIcon(props: IconProps) {
  return (
    <Glyph {...props}>
      <path d="M12 16V4" />
      <path d="m7 9 5-5 5 5" />
      <path d="M4 20h16" />
    </Glyph>
  );
}

/** Remove. A bin rather than a cross, which already means "close" here. */
export function TrashIcon(props: IconProps) {
  return (
    <Glyph {...props}>
      <path d="M3 6h18" />
      <path d="M8 6V4h8v2" />
      <path d="M6 6l1 14h10l1-14" />
    </Glyph>
  );
}

/**
 * The three review verdicts.
 *
 * Drawn as distinct silhouettes rather than three variations on a circle: at
 * 14px, beside a word, in a row of three, the only thing that separates them
 * at a glance is outline. A tick, a cross and a pencil are as far apart as
 * three glyphs of this size get.
 */

/** Accept: the statement stands. */
export function CheckIcon(props: IconProps) {
  return (
    <Glyph {...props}>
      <path d="m20 6-11 11-5-5" />
    </Glyph>
  );
}

/** Reject: the statement is wrong. */
export function CrossIcon(props: IconProps) {
  return (
    <Glyph {...props}>
      <path d="M18 6 6 18" />
      <path d="m6 6 12 12" />
    </Glyph>
  );
}

/** Correct: the statement is wrong and the reviewer supplies the replacement. */
export function PencilIcon(props: IconProps) {
  return (
    <Glyph {...props}>
      <path d="M12 20h9" />
      <path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" />
    </Glyph>
  );
}

/** Copy to clipboard: two sheets, the front one offset. */
export function CopyIcon(props: IconProps) {
  return (
    <Glyph {...props}>
      <rect x="9" y="9" width="11" height="11" rx="2" />
      <path d="M5 15V5a2 2 0 0 1 2-2h10" />
    </Glyph>
  );
}

/** Find something. Lucide's `search`. */
export function SearchIcon(props: IconProps) {
  return (
    <Glyph {...props}>
      <circle cx="11" cy="11" r="8" />
      <path d="m21 21-4.3-4.3" />
    </Glyph>
  );
}

/** An open page. Reading, as against saving -- which is the tray glyph above. */
export function PageIcon(props: IconProps) {
  return (
    <Glyph {...props}>
      <path d="M6 3h7l5 5v13H6z" />
      <path d="M13 3v5h5" />
      <path d="M9 13h6" />
      <path d="M9 17h4" />
    </Glyph>
  );
}

/**
 * The current theme, not the one a click would switch to.
 *
 * A control that names its destination -- a button reading "dark" while the
 * page is light -- is ambiguous in exactly the way a theme switch cannot
 * afford: the reader has to work out whether the word is a state or a verb.
 * An icon showing where you *are*, with the destination in the tooltip,
 * removes the question.
 */
export function SunIcon(props: IconProps) {
  return (
    <Glyph {...props}>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
    </Glyph>
  );
}

export function MoonIcon(props: IconProps) {
  return (
    <Glyph {...props}>
      <path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5Z" />
    </Glyph>
  );
}

/**
 * The affordance that lets a paragraph become a sentence.
 *
 * Several places in this interface carry three or four lines explaining how a
 * figure was produced. The explanation is load-bearing -- it is most of what
 * makes a number checkable -- but printed in full it competes with the number
 * it is about. Behind an (i) it stays one keystroke away and stops shouting.
 */
export function InfoIcon(props: IconProps) {
  return (
    <Glyph {...props}>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v5" />
      <path d="M12 7.6h.01" />
    </Glyph>
  );
}

/** A question waiting to be asked. Leads each suggested opener. */
export function AskIcon(props: IconProps) {
  return (
    <Glyph {...props}>
      <path d="M21 11.5a8.4 8.4 0 0 1-9 8.4 9 9 0 0 1-3.4-.6L3 21l1.8-5A8.3 8.3 0 0 1 4 11.5 8.4 8.4 0 0 1 12.5 3 8.4 8.4 0 0 1 21 11.5Z" />
    </Glyph>
  );
}

/** The grip on a draggable divider. Three dots read as "hold me" at any size. */
export function GripIcon(props: IconProps) {
  return (
    <Glyph {...props}>
      <circle cx="12" cy="6" r="1" />
      <circle cx="12" cy="12" r="1" />
      <circle cx="12" cy="18" r="1" />
    </Glyph>
  );
}
