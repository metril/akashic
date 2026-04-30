import { Component } from "react";
import { Card } from "./Card";
import { Button } from "./Button";

interface Props {
  children: React.ReactNode;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error("ErrorBoundary caught:", error, info);
  }

  reset = () => {
    window.location.reload();
  };

  render() {
    if (this.state.error) {
      return (
        <div className="min-h-screen flex items-center justify-center bg-app p-6">
          <Card padding="lg" className="w-full max-w-md">
            <div className="flex items-center gap-3 mb-3">
              <div className="h-9 w-9 rounded-full bg-rose-50 text-rose-600 dark:bg-rose-500/10 dark:text-rose-300 flex items-center justify-center">
                <svg
                  xmlns="http://www.w3.org/2000/svg"
                  className="h-5 w-5"
                  viewBox="0 0 20 20"
                  fill="currentColor"
                >
                  <path
                    fillRule="evenodd"
                    d="M8.485 2.495c.673-1.167 2.357-1.167 3.03 0l6.28 10.875c.673 1.167-.17 2.625-1.516 2.625H3.72c-1.347 0-2.189-1.458-1.515-2.625L8.485 2.495zM10 6a.75.75 0 01.75.75v3.5a.75.75 0 01-1.5 0v-3.5A.75.75 0 0110 6zm0 9a1 1 0 100-2 1 1 0 000 2z"
                    clipRule="evenodd"
                  />
                </svg>
              </div>
              <h2 className="text-lg font-semibold text-fg">
                Something went wrong
              </h2>
            </div>
            <p className="text-sm text-fg-muted mb-4">
              The page hit an unexpected error. Reloading usually clears it.
            </p>
            <pre className="text-xs bg-app border border-line rounded-md p-3 mb-4 overflow-auto max-h-40 text-rose-700 font-mono">
              {this.state.error.message}
            </pre>
            <Button onClick={this.reset} className="w-full">
              Reload page
            </Button>
          </Card>
        </div>
      );
    }
    return this.props.children;
  }
}
