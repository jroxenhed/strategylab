import { Component, type ReactNode } from 'react'

interface State {
  error: Error | null
}

export default class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('[ErrorBoundary]', error, info.componentStack)
  }

  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 24, color: '#f85149', fontFamily: 'monospace', fontSize: 13, whiteSpace: 'pre-wrap', background: '#0B0E14', minHeight: '100vh' }}>
          <div style={{ fontWeight: 700, marginBottom: 12 }}>Render error — paste this into chat:</div>
          <div>{this.state.error.message}</div>
          <div style={{ marginTop: 12, color: '#8b949e', fontSize: 11 }}>{this.state.error.stack}</div>
          <button
            onClick={() => this.setState({ error: null })}
            style={{ marginTop: 16, padding: '6px 12px', background: '#21262d', color: '#e6edf3', border: '1px solid #30363d', borderRadius: 4, cursor: 'pointer' }}
          >
            Try to recover
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
