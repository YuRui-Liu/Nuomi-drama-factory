import { LoginStageContent } from "@/components/login/login-stage";

export function LoginCinematicHero({
  onStart,
}: {
  heroExitProgress: number;
  onStart: () => void;
}) {
  return <LoginStageContent onStart={onStart} />;
}
