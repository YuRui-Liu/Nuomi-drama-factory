import { useNavigate, useSearch } from "@tanstack/react-router";

export function useCharacterVoiceNavigation() {
  const search = useSearch({ strict: false }) as Record<string, unknown>;
  const navigate = useNavigate();
  const tab = search.characterTab === "voice" || search.characterTab === "profile" ? search.characterTab : "visual";
  const character = typeof search.character === "string" ? search.character : null;
  return {
    tab, character,
    select(name: string, nextTab: string = tab) {
      void navigate({ search: ((prev: Record<string, unknown>) => {
        const next: Record<string, unknown> = { ...prev, character: name, characterTab: nextTab };
        // Select the character asset category without retaining an old identity.
        next.type = "identity";
        delete next.id;
        return next;
      }) as never });
    },
    clear() {
      void navigate({ search: ((prev: Record<string, unknown>) => {
        const next = { ...prev };
        delete next.character; delete next.characterTab; delete next.type; delete next.id;
        return next;
      }) as never });
    },
  };
}
