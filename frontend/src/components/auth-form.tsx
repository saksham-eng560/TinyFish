"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { ArrowUpRight } from "lucide-react";
import { BrushHeadline, Logo, TunnelGrid } from "@/components/brand";
import { useAuthConfig } from "@/components/demo";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, post } from "@/lib/api-client";
import { GOOGLE_SETUP_URL } from "@/lib/site";
import { cn } from "@/lib/utils";

const ERRORS: Record<string, string> = {
  google_oauth_failed: "Google sign-in failed. Please try again.",
  access_denied: "Google sign-in was cancelled.",
  google_email_unverified: "Google hasn't verified this account's email address, so it can't be used to sign in here.",
  account_disabled: "This account is disabled.",
  registration_disabled: "Registration is disabled on this server.",
  session_expired: "Your session expired — sign in again.",
};

/** Google's own "G", in its colours (Google's sign-in branding asks for it unchanged). */
function GoogleG() {
  return (
    <svg viewBox="0 0 48 48" className="h-5 w-5" aria-hidden>
      <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z" />
      <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z" />
      <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z" />
      <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z" />
    </svg>
  );
}

/** "Continue with Google": signs in, or creates the account on first use. Until this computer has a Google
 *  OAuth client in .env, it explains the one-time setup instead. */
function GoogleButton({ next, showSetup, onShowSetup }: { next: string; showSetup: boolean; onShowSetup: () => void }) {
  const { data: config } = useAuthConfig();
  const className = cn(buttonVariants({ variant: "outline", size: "lg" }), "w-full gap-3 bg-background");
  return (
    <div className="space-y-3">
      {config && !config.google_enabled ? (
        <button type="button" className={className} onClick={onShowSetup} aria-expanded={showSetup} aria-controls="google-setup">
          <GoogleG /> Continue with Google
        </button>
      ) : (
        <a href={`/api/v1/auth/google/login?next=${encodeURIComponent(next)}`} className={className}>
          <GoogleG /> Continue with Google
        </a>
      )}
      {showSetup && (
        <div id="google-setup" role="note" className="rounded-lg border border-border bg-secondary p-3 text-sm text-secondary-foreground">
          <p className="font-semibold">Google sign-in isn&apos;t set up on this computer yet.</p>
          <p className="mt-1">
            It&apos;s a one-time, free setup of about five minutes: create a Google OAuth client, then put its ID and secret
            in <code>.env</code> and restart HireFlow.{" "}
            <a href={GOOGLE_SETUP_URL} target="_blank" rel="noreferrer" className="font-semibold text-primary hover:underline">See the steps</a>.
            Until then, use your email below.
          </p>
        </div>
      )}
    </div>
  );
}

export function AuthForm({ mode }: { mode: "login" | "register" }) {
  const router = useRouter();
  const params = useSearchParams();
  const next = params.get("next") || "/dashboard";
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [showGoogleSetup, setShowGoogleSetup] = useState(false);
  const { data: config } = useAuthConfig();
  const google = !config?.demo_mode;  // the demo has no real-world sign-ins

  useEffect(() => {
    const e = params.get("error");
    if (e === "google_not_configured") setShowGoogleSetup(true);
    else if (e) setError(ERRORS[e] || ERRORS.google_oauth_failed);
  }, [params]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      if (mode === "login") await post("/auth/login", { email, password });
      else await post("/auth/register", { email, password, full_name: fullName });
      router.replace(mode === "register" ? "/onboarding" : next);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong");
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="grid min-h-screen bg-background lg:grid-cols-[1.1fr_1fr]">
      <section className="relative hidden flex-col justify-between overflow-hidden border-r border-line/60 p-10 lg:flex">
        <TunnelGrid className="absolute inset-0 opacity-60" />
        <Logo className="relative" />
        <div className="relative">
          <BrushHeadline lines={mode === "login" ? ["Welcome", "back.", "Your deck", "is waiting"] : ["Build your", "agent.", "Then just", "swipe"]}
            className="text-[clamp(2.4rem,4.4vw,4.2rem)]" />
          <p className="mt-8 max-w-md text-foreground/75">
            Thousands of internships, one swipe each. Keep the ones you like — HireFlow tailors, fills and applies.
          </p>
        </div>
        <p className="label-caps relative text-muted-foreground">Nothing is sent for a job you didn&apos;t keep</p>
      </section>
      <section className="flex flex-col">
        <header className="flex h-20 items-center justify-between border-b border-line/60 px-6 sm:px-10">
          <Logo className="lg:invisible" />
          <Link href={mode === "login" ? "/register" : "/login"} className={buttonVariants({ variant: "outline", size: "sm" })}>
            {mode === "login" ? "Create account" : "Sign in"}
          </Link>
        </header>
        <div className="flex flex-1 items-center justify-center p-6 sm:p-10">
          <div className="w-full max-w-sm">
            <p className="label-caps text-primary">{mode === "login" ? "Sign in" : "Get started"}</p>
            <h1 className="display mt-3 text-3xl">{mode === "login" ? "Welcome back" : "Create your account"}</h1>
            <p className="mt-2 text-sm text-muted-foreground">
              {mode === "login" ? "Sign in to your HireFlow dashboard." : "Set up your internship agent in two minutes."}
            </p>
            {google && (
              <>
                <div className="mt-8">
                  <GoogleButton next={next} showSetup={showGoogleSetup} onShowSetup={() => setShowGoogleSetup((open) => !open)} />
                </div>
                <div className="my-6 flex items-center gap-3 text-xs text-muted-foreground">
                  <div className="h-px flex-1 bg-border" /> or with email <div className="h-px flex-1 bg-border" />
                </div>
              </>
            )}
            <form onSubmit={submit} className={cn("space-y-5", !google && "mt-8")}>
              {mode === "register" && (
                <div className="space-y-2">
                  <Label htmlFor="name" className="label-caps">Full name</Label>
                  <Input id="name" value={fullName} onChange={(e) => setFullName(e.target.value)} required autoComplete="name" />
                </div>
              )}
              <div className="space-y-2">
                <Label htmlFor="email" className="label-caps">Email</Label>
                <Input id="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="email" />
              </div>
              <div className="space-y-2">
                <Label htmlFor="password" className="label-caps">Password</Label>
                <Input id="password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} required
                  minLength={mode === "register" ? 8 : undefined} autoComplete={mode === "login" ? "current-password" : "new-password"} />
              </div>
              {error && <p role="alert" className="rounded-lg border border-primary/60 bg-primary/10 p-3 text-sm text-primary">{error}</p>}
              <Button type="submit" size="lg" className="w-full" loading={loading}>
                {mode === "login" ? "Sign in" : "Create account"} <ArrowUpRight />
              </Button>
              {mode === "register" && (
                <p className="text-center text-xs text-muted-foreground">
                  By creating an account you agree to the <Link href="/terms" className="font-semibold text-primary hover:underline">terms</Link> and{" "}
                  <Link href="/responsible-use" className="font-semibold text-primary hover:underline">responsible use</Link>. See how your data is handled in the{" "}
                  <Link href="/privacy" className="font-semibold text-primary hover:underline">privacy policy</Link>.
                </p>
              )}
            </form>
            <p className="mt-6 text-sm text-muted-foreground">
              {mode === "login" ? (
                <>No account? <Link href="/register" className="font-semibold text-foreground underline-offset-4 hover:text-primary hover:underline">Create one</Link></>
              ) : (
                <>Already registered? <Link href="/login" className="font-semibold text-foreground underline-offset-4 hover:text-primary hover:underline">Sign in</Link></>
              )}
            </p>
          </div>
        </div>
      </section>
    </main>
  );
}
