/**
 * Minimal pub/sub toast helper — no React context needed.
 * Any module calls showToast(); the <Toaster> component listens.
 */

export function showToast(message, type = 'success') {
  window.dispatchEvent(new CustomEvent('app:toast', { detail: { message, type } }))
}
