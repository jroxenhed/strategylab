import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    watch: {
      usePolling: true,
    },
    // LAN access from phone/iPad via mDNS — Vite blocks non-IP hostnames unless
    // allowlisted (DNS-rebind protection). '.local' covers the Mac's Bonjour name
    // even if the hostname reshuffles.
    allowedHosts: ['.local'],
  },
  preview: {
    allowedHosts: ['.local'],
  },
})
