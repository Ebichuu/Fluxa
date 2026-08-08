import { Component, Suspense, type ReactNode } from 'react';

interface LazyRouteBoundaryProps {
  children: ReactNode;
  routeKey: string;
}

interface LazyRouteErrorBoundaryState {
  failed: boolean;
}

class LazyRouteErrorBoundary extends Component<LazyRouteBoundaryProps, LazyRouteErrorBoundaryState> {
  state: LazyRouteErrorBoundaryState = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidUpdate(previous: LazyRouteBoundaryProps) {
    if (previous.routeKey !== this.props.routeKey && this.state.failed) {
      this.setState({ failed: false });
    }
  }

  render() {
    if (this.state.failed) {
      return (
        <main className="work-page ops-page">
          <div className="ops-empty" role="alert">
            <strong>页面暂时无法加载</strong>
            <span>页面文件可能已更新，请重新载入后再试。</span>
            <button className="ops-action-button ops-action-button--primary" type="button" onClick={() => window.location.reload()}>
              重新加载页面
            </button>
          </div>
        </main>
      );
    }

    return this.props.children;
  }
}

export function LazyRouteBoundary({ children, routeKey }: LazyRouteBoundaryProps) {
  return (
    <LazyRouteErrorBoundary routeKey={routeKey}>
      <Suspense fallback={<main className="work-page ops-page"><div className="ops-empty" role="status">正在打开页面…</div></main>}>
        {children}
      </Suspense>
    </LazyRouteErrorBoundary>
  );
}
