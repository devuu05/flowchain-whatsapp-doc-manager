type Bubble = { from: "me" | "bot"; text: string; time: string; file?: string };

const CONVERSATION: Bubble[] = [
  { from: "me", text: "invoice from ABC Traders", time: "10:02", file: "scan.pdf · 1 page" },
  { from: "bot", text: "📥 Received scan.pdf. Processing it now…", time: "10:02" },
  { from: "bot", text: "✅ Saved Invoice_ABC-Traders_2026-09-15.pdf\n📁 Invoices/2026 · 🏢 ABC Traders · 📅 15 Sep 2026", time: "10:02" },
  { from: "me", text: "find invoices from ABC Traders", time: "16:40" },
  { from: "bot", text: "🔎 2 results:\n1. Invoice · ABC Traders · 2026-09-15\n2. Invoice · ABC Traders · 2026-08-02\nReply with a number to get the file.", time: "16:40" },
  { from: "me", text: "1", time: "16:41" },
  { from: "bot", text: "", time: "16:41", file: "Invoice_ABC-Traders_2026-09-15.pdf" },
];

/** Static illustration of a WhatsApp conversation with Flow Chain (not live data). */
export function ChatDemo() {
  return (
    <figure className="lp-phone" aria-label="Example WhatsApp conversation with Flow Chain">
      <div className="lp-phone-head">
        <span className="lp-avatar" aria-hidden="true">FC</span>
        <div>
          <strong>Flow Chain Docs</strong>
          <span>online</span>
        </div>
      </div>
      <ol className="lp-chat">
        {CONVERSATION.map((b, i) => (
          <li key={i} className={`lp-bubble lp-${b.from}`}>
            <span className="sr-only">{b.from === "me" ? "You:" : "Flow Chain:"}</span>
            {b.file && (
              <span className="lp-file">
                <span aria-hidden="true">📄</span> {b.file}
              </span>
            )}
            {b.text && <span className="lp-text">{b.text}</span>}
            <time>{b.time}</time>
          </li>
        ))}
      </ol>
    </figure>
  );
}
