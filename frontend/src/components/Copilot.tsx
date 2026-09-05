/**
 * The grounded chat.
 *
 * The one thing this adds over a plain chat box: every answer shows the tools
 * it was built from, and says so when it was built from none. An ungrounded
 * answer is not necessarily wrong -- "hello" needs no tool -- but in a tool
 * whose whole claim is that it does not invent things, the reader is entitled
 * to know which kind of answer they are looking at without having to ask.
 *
 * Three things the previous version got wrong, all of them the sort a person
 * only notices while using it:
 *
 *   * There was no send control at all. Enter worked; a thumb did not, and
 *     nothing on screen said Enter was the way.
 *   * A question vanished the moment it was asked -- cleared from the input,
 *     not yet in the thread -- so the one second where the user most wants
 *     confirmation that anything happened showed them nothing.
 *   * Question and answer rendered as two identical paragraphs, which is
 *     unreadable the moment a thread is longer than one exchange.
 */
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { Button, Chip, Info } from "@/components/primitives";
import {
  AlertIcon,
  AskIcon,
  ChevronIcon,
  CloseIcon,
  RetryIcon,
  SendIcon,
} from "@/components/icons";
import { RichText } from "@/components/RichText";
import { cx } from "@/lib/cx";

interface Turn {
  question: string;
  answer: string;
  grounded: boolean;
  /** Answered without a language model -- a greeting, or "what can you do".
   *  Carries no claim about the model, so it gets no "no tool used" badge. */
  conversational?: boolean;
  tools: string[];
  rejected: string[];
  failed?: boolean;
}

/** Deliberately model-agnostic. The examples here used to name measures from
 *  one particular sample model, so on any other model the interface opened by
 *  suggesting three questions about things that did not exist.
 *
 *  Chosen to reach four different tools rather than four phrasings of the same
 *  one. An opener is the only advertisement these capabilities get: nobody
 *  guesses that a documentation assistant can read row-level security or trace
 *  a table back to the file it loads from, so a set of openers that all ask
 *  about measures leaves most of the surface undiscovered. */
const OPENERS = [
  "What does this model report on?",
  "Anything structurally risky a reviewer should look at?",
  "Where does the data come from, and what changes it on the way in?",
  "Who can see what — are there security roles?",
];

export function Copilot({
  model,
  onClose,
}: {
  model: string;
  /** Supplied only when the panel floats over the work and needs dismissing. */
  onClose?: () => void;
}) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [question, setQuestion] = useState("");
  /** The question currently in flight, echoed into the thread immediately. */
  const [pending, setPending] = useState<string | null>(null);
  const log = useRef<HTMLDivElement>(null);
  const composer = useRef<HTMLTextAreaElement>(null);

  const busy = pending !== null;

  // Layout effect, not effect: scrolling after the browser has painted the new
  // turn produces a visible jump. This runs before paint, so the thread is
  // simply already at the bottom.
  useLayoutEffect(() => {
    const node = log.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [turns, pending]);

  // The composer grows with the question up to a ceiling, then scrolls. A
  // fixed one-line input made anyone pasting a DAX expression edit it blind.
  useEffect(() => {
    const node = composer.current;
    if (!node) return;
    node.style.height = "0px";
    node.style.height = `${Math.min(node.scrollHeight, 132)}px`;
  }, [question]);

  async function ask(asked: string) {
    const text = asked.trim();
    if (!text || busy) return;

    setQuestion("");
    setPending(text);
    const result = await api.ask(text);
    setPending(null);

    setTurns((previous) => [
      ...previous,
      result.ok
        ? {
            question: text,
            answer: result.data.answer,
            grounded: result.data.grounded,
            conversational: result.data.conversational,
            tools: result.data.tool_calls.map((call) => call.name),
            rejected: result.data.rejected_calls,
          }
        : {
            question: text,
            answer: result.message,
            grounded: false,
            tools: [],
            rejected: [],
            failed: true,
          },
    ]);
    composer.current?.focus();
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    // Enter sends, Shift+Enter breaks the line -- the convention every chat
    // shares, and the reason the hint below the composer says so out loud.
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void ask(question);
    }
  }

  return (
    <div className="flex min-h-0 flex-col">
      <header className="flex flex-none items-center gap-2 border-b border-hairline px-3.5 py-2">
        <h2 className="font-mono text-[11px] tracking-[0.1em] text-faint uppercase">
          Copilot
        </h2>
        {model && (
          <span className="min-w-0 truncate font-mono text-[11px] text-faint">
            · {model}
          </span>
        )}
        {onClose && (
          <Button
            size="icon"
            onClick={onClose}
            aria-label="Close the copilot"
            className="ml-auto"
          >
            <CloseIcon />
          </Button>
        )}
      </header>

      {/* `log` rather than `region`: this is an append-only transcript, and the
          role tells a screen reader to announce additions without stealing
          focus from the composer the user is still typing in. */}
      <div
        ref={log}
        role="log"
        aria-live="polite"
        aria-busy={busy}
        aria-label="Conversation"
        className="min-h-0 flex-1 overflow-y-auto px-3.5 py-3"
      >
        {turns.length === 0 && !busy && (
          <div className="flex flex-col gap-3">
            {/* One line, and the rest behind the (i). The claim it makes --
                that answers come from the graph rather than from the model's
                memory -- is the whole reason to trust this panel, and it was
                being read once and then skipped forever. */}
            <p className="flex items-center gap-1.5 text-sm text-muted">
              Ask about this model.
              <Info label="where these answers come from">
                Every answer is assembled from tools run against the extracted
                graph — never recalled from the language model&rsquo;s memory. Each
                reply lists the tools it called, so you can check what it read
                before you believe what it wrote.
              </Info>
            </p>

            <p className="font-mono text-[10px] tracking-[0.08em] text-faint uppercase">
              Try one of these
            </p>
            <ul className="flex flex-col gap-1">
              {OPENERS.map((opener) => (
                <li key={opener}>
                  {/* Clickable, because a suggestion you have to retype is a
                      decoration rather than a shortcut.

                      Drawn as a row rather than a bordered box: four boxes
                      stacked read as four empty form fields waiting to be
                      filled in, which is the opposite of what they are. A rail
                      that lights on hover and an arrow that arrives with it say
                      "this goes somewhere" without spending a border. */}
                  <button
                    onClick={() => {
                      setQuestion(opener);
                      composer.current?.focus();
                    }}
                    className={cx(
                      "group flex w-full cursor-pointer items-start gap-2 rounded-md",
                      "border border-transparent py-1.5 pr-2 pl-2 text-left",
                      "text-[12.5px] leading-snug text-muted",
                      "transition-[color,background-color,border-color]",
                      "duration-(--duration-feedback) ease-(--ease-standard)",
                      "hover:border-hairline hover:bg-raised hover:text-ink",
                      "focus-visible:outline-none focus-visible:border-accent/50",
                      "focus-visible:bg-accent-soft focus-visible:text-accent",
                    )}
                  >
                    <AskIcon
                      size={13}
                      className="mt-[3px] flex-none text-faint transition-colors group-hover:text-accent"
                    />
                    <span className="min-w-0 flex-1">{opener}</span>
                    <ChevronIcon
                      size={13}
                      className={cx(
                        "mt-[3px] flex-none -translate-x-1 text-faint opacity-0",
                        "transition-[opacity,transform] duration-(--duration-feedback)",
                        "ease-(--ease-standard) group-hover:translate-x-0",
                        "group-hover:opacity-100 group-focus-visible:opacity-100",
                      )}
                    />
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="space-y-5">
          {turns.map((turn, index) => (
            <Exchange
              key={index}
              turn={turn}
              // Only the newest turn animates in. Replaying the whole thread on
              // every render would be motion for its own sake, and re-reading
              // an earlier answer should not make it move.
              fresh={index === turns.length - 1}
              onRetry={turn.failed ? () => void ask(turn.question) : undefined}
            />
          ))}

          {pending !== null && (
            <div className="space-y-2">
              <Question>{pending}</Question>
              <Thinking />
            </div>
          )}
        </div>
      </div>

      <form
        onSubmit={(event) => {
          event.preventDefault();
          void ask(question);
        }}
        className="flex-none border-t border-hairline p-2.5"
      >
        <div
          className={cx(
            "flex items-end gap-1.5 rounded border border-hairline bg-surface p-1.5",
            "transition-colors duration-(--duration-feedback) ease-(--ease-standard)",
            "focus-within:border-accent/50",
          )}
        >
          <textarea
            ref={composer}
            rows={1}
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Ask about this model"
            aria-label="Ask about this model"
            className={cx(
              "min-h-6 flex-1 resize-none bg-transparent px-1 py-1 text-sm",
              "placeholder:text-faint focus:outline-none",
            )}
          />
          <Button
            type="submit"
            tone={question.trim() && !busy ? "primary" : "quiet"}
            size="icon"
            disabled={!question.trim() || busy}
            // The icon carries no text, so the button has to carry the name.
            aria-label={busy ? "Waiting for an answer" : "Send question"}
            className="flex-none"
          >
            <SendIcon />
          </Button>
        </div>
        {/* The keystrokes stay; the paragraph about provenance moves behind
            the (i). Four lines of grey monospace under a text box is a wall a
            reader learns to look past, which is a poor place for the sentence
            that says how much to trust the answer. */}
        <p className="mt-1.5 flex items-center gap-1.5 px-1 font-mono text-[10.5px] text-faint">
          <kbd className="font-mono">Enter</kbd> to send ·{" "}
          <kbd className="font-mono">Shift+Enter</kbd> for a new line
          <Info label="how answers are written" align="right">
            Answers are written by a language model from the results of tools it
            ran against this model&rsquo;s extracted graph. Every tool it called is
            listed under the reply, so the working is checkable rather than
            taken on trust.
          </Info>
        </p>
      </form>
    </div>
  );
}

/** Names in first-seen order, each with how many times it was called. */
function tally(names: string[]): [string, number][] {
  const counts = new Map<string, number>();
  for (const name of names) counts.set(name, (counts.get(name) ?? 0) + 1);
  return [...counts.entries()];
}

/** The user's turn. Set apart by a surface, not by a coloured bubble -- this is
 *  an audit tool, and the palette's colours all mean something already. */
function Question({ children }: { children: string }) {
  return (
    <p className="rounded rounded-br-sm bg-raised px-2.5 py-1.5 text-sm font-medium break-words text-ink">
      {children}
    </p>
  );
}

function Exchange({
  turn,
  fresh,
  onRetry,
}: {
  turn: Turn;
  fresh: boolean;
  onRetry?: () => void;
}) {
  return (
    <div className={cx("space-y-2", fresh && "animate-(--animate-rise)")}>
      <Question>{turn.question}</Question>

      {turn.failed ? (
        <div className="rounded border border-bad/40 bg-bad-soft px-2.5 py-2">
          {/* `break-words` is load-bearing, not defensive. A provider error
              carries billing URLs and model identifiers with no spaces in
              them, and without this they run straight out through the side of
              the panel -- which is exactly how this was found. */}
          <p className="flex items-start gap-1.5 text-sm text-bad">
            <AlertIcon className="mt-0.5 flex-none" />
            <span className="min-w-0 break-words">{turn.answer}</span>
          </p>
          {onRetry && (
            <Button tone="bad" onClick={onRetry} className="mt-2">
              <RetryIcon size={12} />
              Try again
            </Button>
          )}
        </div>
      ) : (
        // The model marks measure names as `**Bold**`, and rendering that
        // verbatim ran raw asterisks through the middle of every answer that
        // listed anything -- which is most of them. `RichText` already exists
        // for precisely this and sets a marked name in the monospace face
        // object names use everywhere else, so the emphasis reads as "this is
        // a thing in the model" rather than as shouting.
        <p className="text-sm leading-relaxed break-words whitespace-pre-wrap text-muted">
          <RichText>{turn.answer}</RichText>
        </p>
      )}

      {(turn.tools.length > 0 ||
        turn.rejected.length > 0 ||
        (!turn.grounded && !turn.conversational)) &&
        !turn.failed && (
          <div className="flex flex-wrap items-center gap-1">
            {/* Counted, not repeated. Answering "which measures compute a
                rate" calls describe_measure once per measure, and five
                identical chips in a row said nothing the count does not say
                more legibly -- while pushing the two chips that carry a
                warning off the end of the row. Order is preserved: which tool
                was reached for first is part of how the answer was built. */}
            {tally(turn.tools).map(([tool, count]) => (
              <Chip key={tool} tone="accent" title="Read from the model">
                {tool}
                {count > 1 && <span className="opacity-70">×{count}</span>}
              </Chip>
            ))}
            {!turn.grounded && !turn.conversational && turn.tools.length === 0 && (
              <Chip tone="review" title="Answered without reading the model">
                no tool used
              </Chip>
            )}
            {turn.rejected.map((name, position) => (
              <Chip
                key={`${name}-${position}`}
                tone="bad"
                title="The model asked for a tool that does not exist; the request was refused."
              >
                refused {name}
              </Chip>
            ))}
          </div>
        )}
    </div>
  );
}

/** The wait, made legible.
 *
 *  The word carries the meaning and the dots only decorate it, which is what
 *  makes this safe under `prefers-reduced-motion`: the animation is switched
 *  off entirely there and the state is still perfectly clear. */
function Thinking() {
  return (
    <p className="flex items-center gap-2 text-sm text-faint">
      <span className="flex gap-1" aria-hidden="true">
        {[0, 1, 2].map((index) => (
          <span
            key={index}
            className="size-1.5 rounded-full bg-faint animate-(--animate-thinking)"
            style={{ animationDelay: `${index * 160}ms` }}
          />
        ))}
      </span>
      Thinking…
    </p>
  );
}
