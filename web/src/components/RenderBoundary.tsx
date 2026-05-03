import { Component, type ReactNode } from "react";

interface Props {
  /** Shown in place of the children when a render error is caught.
   *  Receives the caught error so the fallback UI can decide what
   *  to say (or quietly swallow it). */
  fallback: (err: Error) => ReactNode;
  children: ReactNode;
}

interface State {
  err: Error | null;
}

/**
 * Catch-and-replace boundary for render errors below this point. Used
 * around the WebGL2 treemap so a context-lost / driver-init crash
 * degrades to a fallback view rather than blanking the page.
 *
 * Deliberately minimal: no telemetry, no recovery button. The caller's
 * `fallback` does whatever it needs to.
 */
export class RenderBoundary extends Component<Props, State> {
  state: State = { err: null };

  static getDerivedStateFromError(err: Error): State {
    return { err };
  }

  componentDidCatch(err: Error) {
    // Surface to console — operators reading devtools want the stack,
    // not just the user-facing fallback string.
    console.error("RenderBoundary caught:", err);
  }

  render() {
    if (this.state.err) return this.props.fallback(this.state.err);
    return this.props.children;
  }
}
