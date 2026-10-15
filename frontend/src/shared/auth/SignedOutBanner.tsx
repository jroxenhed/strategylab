import { signInHref, useSignedOut } from './signedOut'

/**
 * Full-width banner shown at the top of the app once the sign-in has
 * expired. The link is a plain same-origin link to the sign-in proxy, which
 * brings the user back to the page they were on.
 */
export default function SignedOutBanner() {
  const signedOut = useSignedOut()
  if (!signedOut) return null
  return (
    <div role="alert" data-testid="signed-out-banner" style={styles.bar}>
      <span>Your sign-in has expired. Sign in again to see live data.</span>
      <a href={signInHref()} style={styles.link}>Sign in again</a>
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  bar: {
    display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 16,
    width: '100%', boxSizing: 'border-box', padding: '8px 16px',
    background: '#1f2a3a', color: '#e6edf3', fontSize: 13,
    borderBottom: '1px solid #388bfd', flexShrink: 0,
  },
  link: {
    color: '#0d1117', background: '#58a6ff', borderRadius: 4,
    padding: '3px 10px', fontWeight: 600, textDecoration: 'none',
  },
}
