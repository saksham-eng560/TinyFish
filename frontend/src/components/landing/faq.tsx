import { ChevronDown } from "lucide-react";

export const FAQS: { q: string; a: string }[] = [
  {
    q: "Will it spam employers in my name?",
    a: "No. Only jobs you keep are prepared, and each one waits 10 minutes in \"Sending soon\" so you can cancel it. The " +
      "server caps applications at 25 a day and 3 per company a week, and never applies twice to the same job.",
  },
  {
    q: "Does it make things up on my resume?",
    a: "No. A tailored resume may only reorder and reword what is already on yours. A truthfulness check compares it " +
      "with your original and reverts any skill, title, date or number that isn't there.",
  },
  {
    q: "What about visa, work authorization and background questions?",
    a: "They are never guessed and never sent to an AI model. The form waits for you until you answer once; your " +
      "saved answers are reused after that.",
  },
  {
    q: "Which sites does it apply on?",
    a: "Greenhouse, Lever, Ashby and Workday forms, plus most careers pages. LinkedIn, Internshala, Indeed and " +
      "Glassdoor forbid automation in their terms, so they stay off unless you turn them on and accept the risk.",
  },
  {
    q: "Can I see what was sent?",
    a: "Yes. Every application keeps a screenshot of the filled form, the resume and cover letter it used, and each " +
      "AI-written answer, which you can edit before it goes out. Dry run fills forms without ever pressing Submit.",
  },
  {
    q: "Can I sign in with Google?",
    a: "Yes. “Continue with Google” signs you in, or creates your account the first time, once the computer " +
      "running HireFlow has a free Google OAuth client set up (about five minutes, once). Email and password always work.",
  },
  {
    q: "Which AI does it use, and what does it cost?",
    a: "Anthropic, OpenAI or a free local model through Ollama, whichever you set up, with a fallback between them " +
      "and a daily per-user cap. Without any model it still works on built-in heuristics.",
  },
  {
    q: "Where does my data live?",
    a: "On the server you run it on. You can download everything (JSON plus your files) or delete your account, " +
      "which removes every row and file, from Settings.",
  },
];

/** Plain <details> elements: they work without JavaScript and with the keyboard. */
export function Faq() {
  return (
    <div className="divide-y rounded-2xl border bg-card">
      {FAQS.map((item) => (
        <details key={item.q} className="group px-5 sm:px-6">
          <summary className="flex cursor-pointer list-none items-center justify-between gap-4 py-5 text-left font-semibold
            focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-details-marker]:hidden">
            {item.q}
            <ChevronDown className="h-4 w-4 shrink-0 text-primary transition-transform group-open:rotate-180" aria-hidden />
          </summary>
          <p className="-mt-1 pb-5 leading-relaxed text-muted-foreground">{item.a}</p>
        </details>
      ))}
    </div>
  );
}
