import { useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { completeSsoLogin } from "../api/client";
import { Spinner } from "../components/ui";

export default function AuthCallback() {
  const navigate = useNavigate();
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    completeSsoLogin().then((ok) => {
      navigate(ok ? "/dashboard" : "/login?error=sso_failed", { replace: true });
    });
  }, [navigate]);

  return (
    <div className="flex items-center justify-center h-screen text-fg-subtle">
      <Spinner />
    </div>
  );
}
