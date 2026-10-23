import type { Metadata } from "next";
import Link from "next/link";

import { ChatDemo } from "@/components/landing/ChatDemo";

import "./landing.css";

export const metadata: Metadata = {
  title: "Flow Chain — WhatsApp Document Manager",
  description:
    "Send business documents on WhatsApp. Flow Chain reads them, names them, files them in your own Google Drive and sends them back when you ask.",
  robots: { index: true, follow: true },
};

// Reads CONTACT_EMAIL at request time so the same image works for any deployment.
export const dynamic = "force-dynamic";

const STEPS = [
  {
    n: "1",
    title: "Send it on WhatsApp",
    body: "Staff send a photo, PDF or Office file to your Flow Chain number with a short note like “invoice from ABC Traders”. No caption? Flow Chain asks what it is.",
  },
  {
    n: "2",
    title: "Flow Chain files it",
    body: "It checks the file, reads it, names it properly (Invoice_ABC-Traders_2026-09-15.pdf) and saves the original into the right folder of your Google Drive — then confirms only once it is really saved.",
  },
  {
    n: "3",
    title: "Ask for it back",
    body: "Type “find invoices from ABC Traders” or “documents from September 2026”. Reply with the number from the list and the file arrives in the chat.",
  },
];

const FEATURES = [
  {
    icon: "🧾",
    title: "Understands your paperwork",
    body: "Recognises the document type, company or person, and date or period. When it isn’t sure, it says “Unknown” and marks the file for review instead of guessing.",
  },
  {
    icon: "🗂️",
    title: "A tidy Google Drive, automatically",
    body: "Every file lands in Category / Year folders with a consistent name. The original file is kept exactly as sent.",
  },
  {
    icon: "🔎",
    title: "Search in plain language",
    body: "Find by type, company, month, year or file name — or just “show my latest documents”. Results are numbered so anyone can pick one.",
  },
  {
    icon: "💬",
    title: "Built for real WhatsApp habits",
    body: "Handles missing captions, several files at once, the same file sent twice and replies like “2: receipt from Dev Construction”.",
  },
  {
    icon: "🛡️",
    title: "Safe with every file",
    body: "Files are checked by their actual content, not just the extension. Macros, executables, oversized and broken files are refused with a clear reply.",
  },
  {
    icon: "🔁",
    title: "Reliable behind the scenes",
    body: "Automatic retries, no duplicate uploads or replies, and every failure is visible in the dashboard with a one-click retry.",
  },
];

const FORMATS = ["Photos (JPEG, PNG, WebP)", "PDF", "Word (DOCX)", "Excel (XLSX, XLS)", "PowerPoint (PPTX)", "Text (TXT)", "CSV", "DOC & PPT (stored as originals)"];
const CATEGORIES = ["Invoice", "Receipt", "Bill", "Cheque", "Purchase Order", "Contract", "Bank Statement", "ID / KYC", "Report", "Certificate", "Other"];

const DASHBOARD = [
  "Search, filter and open every document, with corrections when details are wrong",
  "See failures and retry them in one click",
  "Connect your WhatsApp number and Google Drive",
  "Choose categories and which ones may ever be sent to AI",
  "Add team members and decide which phone numbers may use the bot",
  "Usage and monthly limits at a glance",
  "Full audit log of sign-ins, downloads, deliveries and changes",
  "Export your data or delete it — whenever you choose",
];

const SECURITY = [
  { title: "Only approved numbers", body: "The bot answers only the staff numbers your admin authorises. Everyone else is ignored and logged." },
  { title: "Your Drive, your files", body: "Documents are stored in your own Google account. Flow Chain can only see the files it creates there and never makes them public." },
  { title: "AI only with your consent", body: "AI classification is off by default. Your workspace owner must switch it on and record who approved it. ID, bank and cheque documents are never sent to AI unless you allow it." },
  { title: "Locked-down by design", body: "Encrypted connection secrets, strict separation between businesses, signed links that expire in minutes, and an audit trail for every sensitive action." },
];

const FAQ = [
  {
    q: "Do my staff need to install anything?",
    a: "No. They use the WhatsApp they already have and message your Flow Chain number like any other contact.",
  },
  {
    q: "Where are our documents stored?",
    a: "In your own Google Drive, inside a “Flow Chain Documents” folder. Flow Chain keeps only the details needed for searching (type, company, date, file name), not the text of your documents.",
  },
  {
    q: "Who can send or request documents?",
    a: "Only the phone numbers your admin has authorised. Messages from any other number get no reply.",
  },
  {
    q: "Is AI reading our documents?",
    a: "Not unless your workspace owner turns it on. Without AI, Flow Chain classifies documents from your description, the file name and the text it can read itself — nothing is sent to an AI service.",
  },
  {
    q: "What happens if a file can’t be read?",
    a: "The original is still saved whenever it is a supported type, marked for review, and you can correct its details in the dashboard. Unsupported or unsafe files are refused with a message explaining why.",
  },
  {
    q: "Can we take our data with us?",
    a: "Yes. Your files are already in your Drive, and the owner can export all records as a file or permanently delete them from the dashboard at any time.",
  },
  {
    q: "Is this the official WhatsApp Business Platform?",
    a: "No. Flow Chain links a regular WhatsApp number the same way WhatsApp Web does. We recommend a dedicated number for your business documents.",
  },
];

export default function LandingPage() {
  const contact = process.env.CONTACT_EMAIL?.trim();
  const pilotHref = contact
    ? `mailto:${contact}?subject=${encodeURIComponent("Flow Chain pilot request")}`
    : "#how";

  return (
    <div className="lp">
      <header className="lp-nav">
        <div className="lp-wrap lp-nav-inner">
          <Link href="/" className="lp-logo" aria-label="Flow Chain home">
            <span className="lp-logo-mark" aria-hidden="true">FC</span>
            Flow Chain
          </Link>
          <nav aria-label="Sections" className="lp-nav-links">
            <a href="#how">How it works</a>
            <a href="#features">Features</a>
            <a href="#security">Security</a>
            <a href="#faq">FAQ</a>
          </nav>
          <Link href="/login" className="lp-btn lp-btn-ghost">Sign in</Link>
        </div>
      </header>

      <main id="main">
        <section className="lp-hero">
          <div className="lp-wrap lp-hero-grid">
            <div>
              <p className="lp-eyebrow">WhatsApp Document Manager</p>
              <h1>Send it on WhatsApp.<br /><span>Find it in seconds.</span></h1>
              <p className="lp-lead">
                Flow Chain turns a WhatsApp number into your business&apos;s filing clerk. Your team sends invoices,
                receipts, cheques and contracts as photos or files — Flow Chain reads them, names them properly, files
                them in your own Google Drive, and sends any document back the moment someone asks.
              </p>
              <div className="lp-cta">
                <a href={pilotHref} className="lp-btn lp-btn-primary">{contact ? "Request a pilot" : "See how it works"}</a>
                <Link href="/login" className="lp-btn lp-btn-ghost">Sign in to your workspace</Link>
              </div>
              <ul className="lp-ticks" aria-label="Highlights">
                <li>No app to install</li>
                <li>Files stay in your Google Drive</li>
                <li>Private by default</li>
              </ul>
            </div>
            <ChatDemo />
          </div>
        </section>

        <section id="how" className="lp-section">
          <div className="lp-wrap">
            <p className="lp-eyebrow">How it works</p>
            <h2>Three steps. Zero filing.</h2>
            <ol className="lp-steps">
              {STEPS.map((s) => (
                <li key={s.n} className="lp-card">
                  <span className="lp-step-n" aria-hidden="true">{s.n}</span>
                  <h3>{s.title}</h3>
                  <p>{s.body}</p>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section id="features" className="lp-section lp-alt">
          <div className="lp-wrap">
            <p className="lp-eyebrow">Features</p>
            <h2>Everything your documents need — in the app you already use</h2>
            <div className="lp-grid-3">
              {FEATURES.map((f) => (
                <article key={f.title} className="lp-card">
                  <span className="lp-icon" aria-hidden="true">{f.icon}</span>
                  <h3>{f.title}</h3>
                  <p>{f.body}</p>
                </article>
              ))}
            </div>
          </div>
        </section>

        <section className="lp-section">
          <div className="lp-wrap lp-split">
            <div>
              <p className="lp-eyebrow">What you can send</p>
              <h2>Real business documents, sorted the way you work</h2>
              <p className="lp-muted">
                Files up to 16 MB by default. Categories are yours to rename, switch off or extend — each one gets its
                own Drive folder.
              </p>
            </div>
            <div>
              <h3 className="lp-chip-title">File types</h3>
              <ul className="lp-chips">{FORMATS.map((f) => <li key={f}>{f}</li>)}</ul>
              <h3 className="lp-chip-title">Categories out of the box</h3>
              <ul className="lp-chips lp-chips-accent">{CATEGORIES.map((c) => <li key={c}>{c}</li>)}</ul>
            </div>
          </div>
        </section>

        <section className="lp-section lp-alt">
          <div className="lp-wrap lp-split">
            <div>
              <p className="lp-eyebrow">Admin dashboard</p>
              <h2>Full control for whoever runs the office</h2>
              <ul className="lp-checklist">{DASHBOARD.map((d) => <li key={d}>{d}</li>)}</ul>
            </div>
            <figure className="lp-preview" aria-label="Illustrative dashboard preview">
              <div className="lp-preview-bar"><span /><span /><span /></div>
              <div className="lp-preview-stats">
                <div><strong>Stored</strong><span>Documents</span></div>
                <div><strong>Review</strong><span>Needs a look</span></div>
                <div><strong>Failures</strong><span>One-click retry</span></div>
              </div>
              <table>
                <thead><tr><th>File</th><th>Type</th><th>Status</th></tr></thead>
                <tbody>
                  <tr><td>Invoice_ABC-Traders_2026-09-15.pdf</td><td>Invoice</td><td><em className="ok">Stored</em></td></tr>
                  <tr><td>Receipt_Dev-Construction_2026-08.jpg</td><td>Receipt</td><td><em className="ok">Stored</em></td></tr>
                  <tr><td>scan.pdf</td><td>—</td><td><em className="warn">Awaiting description</em></td></tr>
                </tbody>
              </table>
              <figcaption>Illustration — your dashboard shows your own documents.</figcaption>
            </figure>
          </div>
        </section>

        <section id="security" className="lp-section">
          <div className="lp-wrap">
            <p className="lp-eyebrow">Security &amp; privacy</p>
            <h2>Your documents stay yours</h2>
            <div className="lp-grid-2">
              {SECURITY.map((s) => (
                <article key={s.title} className="lp-card lp-card-flat">
                  <h3>{s.title}</h3>
                  <p>{s.body}</p>
                </article>
              ))}
            </div>
          </div>
        </section>

        <section className="lp-section lp-alt">
          <div className="lp-wrap">
            <p className="lp-eyebrow">Who it&apos;s for</p>
            <h2>Any team that already shares documents on WhatsApp</h2>
            <div className="lp-grid-4">
              {[
                ["Accounts & finance", "Invoices, receipts, bills and bank statements filed the day they arrive."],
                ["Site & field teams", "Purchase orders, delivery notes and photos sent straight from the site."],
                ["Traders & distributors", "Find any supplier’s invoice in seconds — from your phone."],
                ["Service businesses", "Contracts, certificates and KYC documents, organised and private."],
              ].map(([t, b]) => (
                <article key={t} className="lp-card lp-card-flat">
                  <h3>{t}</h3>
                  <p>{b}</p>
                </article>
              ))}
            </div>
          </div>
        </section>

        <section id="faq" className="lp-section">
          <div className="lp-wrap lp-faq-wrap">
            <p className="lp-eyebrow">FAQ</p>
            <h2>Questions, answered</h2>
            <div className="lp-faq">
              {FAQ.map((f) => (
                <details key={f.q}>
                  <summary>{f.q}</summary>
                  <p>{f.a}</p>
                </details>
              ))}
            </div>
          </div>
        </section>

        <section className="lp-section">
          <div className="lp-wrap">
            <div className="lp-band">
              <div>
                <h2>Stop scrolling through chats to find that one invoice.</h2>
                <p>Start with a pilot for your business and keep every document one message away.</p>
              </div>
              <div className="lp-cta">
                {contact && <a href={pilotHref} className="lp-btn lp-btn-light">Request a pilot</a>}
                <Link href="/login" className="lp-btn lp-btn-outline-light">Sign in</Link>
              </div>
            </div>
          </div>
        </section>
      </main>

      <footer className="lp-footer">
        <div className="lp-wrap lp-footer-inner">
          <span>© {new Date().getFullYear()} Flow Chain</span>
          <nav aria-label="Footer">
            <a href="#how">How it works</a>
            <a href="#security">Security</a>
            <a href="#faq">FAQ</a>
            {contact && <a href={`mailto:${contact}`}>Contact</a>}
            <Link href="/login">Sign in</Link>
          </nav>
        </div>
      </footer>
    </div>
  );
}
