"use client";

import { useCallback, useState } from "react";
import useSWR, { useSWRConfig } from "swr";
import { useToast } from "@/components/ui/toast";
import { ApiError, fetcher, post } from "@/lib/api-client";
import type { FinderRequest, FinderRunItem, FinderSaveResponse, FinderState, Paginated, TinyFishStatus } from "@/lib/types";

export const RECENT_SEARCHES_KEY = "/agent/runs?run_type=tinyfish_search&page_size=5";

/** Whether the server has a TinyFish key, which endpoints are on, and today's usage. */
export function useTinyFishStatus() {
  return useSWR<TinyFishStatus>("/tinyfish/status", fetcher, { revalidateOnFocus: true });
}

/** One Finder search. Polls every 1.5 s while it runs (the `tinyfish_progress` WebSocket event usually beats it). */
export function useFinderSearch(id: string | null) {
  return useSWR<FinderState>(id ? `/tinyfish/search/${id}` : null, fetcher, {
    refreshInterval: (state?: FinderState) => (state?.status === "running" ? 1500 : 0),
  });
}

/** The last few Finder searches, newest first, for re-opening. */
export function useRecentSearches() {
  return useSWR<Paginated<FinderRunItem>>(RECENT_SEARCHES_KEY, fetcher);
}

export const startSearch = (request: FinderRequest) =>
  post<{ search_id: string; status: string; started_at: string }>("/tinyfish/search", request);

export const saveListings = (searchId: string, listingIds: string[]) =>
  post<FinderSaveResponse>("/tinyfish/save", { search_id: searchId, listing_ids: listingIds });

const message = (err: unknown) => (err instanceof ApiError ? err.message : String(err));

/** Start / stop / save for the Finder page, with the toasts. `searchId` is the search the page is showing. */
export function useFinder() {
  const toast = useToast();
  const { mutate } = useSWRConfig();
  const [searchId, setSearchId] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [saving, setSaving] = useState(false);
  const search = useFinderSearch(searchId);
  const { mutate: refreshSearch } = search;

  const start = useCallback(async (request: FinderRequest) => {
    setStarting(true);
    try {
      const { search_id } = await startSearch(request);
      setSearchId(search_id);
      setStopping(false);
      void mutate(RECENT_SEARCHES_KEY);
      void mutate("/agent/status");
      return true;
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        toast({ title: "A search is already running", description: err.message, tone: "info" });
      } else if (err instanceof ApiError && err.status === 429) {
        toast({ title: "Slow down a little", description: "Too many searches in a short time. Try again in a minute.", tone: "info" });
      } else {
        toast({ title: "Could not start the search", description: message(err), tone: "error" });
      }
      return false;
    } finally {
      setStarting(false);
    }
  }, [mutate, toast]);

  const stop = useCallback(async () => {
    if (!searchId) return;
    setStopping(true);
    try {
      await post(`/agent/runs/${searchId}/cancel`);
      toast({ title: "Stopping the search", description: "What was found so far is ranked and kept.", tone: "info" });
      await refreshSearch();
    } catch (err) {
      setStopping(false);
      toast({ title: "Could not stop the search", description: message(err), tone: "error" });
    }
  }, [searchId, refreshSearch, toast]);

  /** Save listings into Swipe Review. Returns the server's answer, or null when it failed. */
  const save = useCallback(async (ids: string[]) => {
    if (!searchId || !ids.length) return null;
    setSaving(true);
    try {
      const result = await saveListings(searchId, ids);
      const byId = new Map(result.items.map((i) => [i.listing_id, i]));
      await refreshSearch((current) => current && {
        ...current,
        results: current.results?.map((l) => (byId.has(l.id) ? { ...l, saved: true, application_id: byId.get(l.id)?.application_id ?? l.application_id } : l)),
      }, { revalidate: true });
      void mutate((key) => typeof key === "string" && ["/review", "/applications", "/jobs", "/agent/status"].some((p) => key.startsWith(p)));
      const total = result.saved + result.already_in_deck;
      toast({
        title: total === 1 ? "Saved to Swipe Review" : `Saved ${total} listings to Swipe Review`,
        description: result.already_in_deck ? `${result.already_in_deck} already in your deck.` : "Open Swipe Review to keep or skip them.",
        tone: "success",
      });
      return result;
    } catch (err) {
      toast({ title: "Could not save", description: message(err), tone: "error" });
      return null;
    } finally {
      setSaving(false);
    }
  }, [searchId, refreshSearch, mutate, toast]);

  return { searchId, setSearchId, search, starting, stopping, saving, start, stop, save };
}
