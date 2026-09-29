/** Saves `content` as a file in the browser, without a server round trip. */
export function downloadTextFile(filename: string, mimeType: string, content: string): void {
  const url = URL.createObjectURL(new Blob([content], { type: mimeType }))
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  // Revoke after the click has been handled; some browsers cancel a download
  // whose object URL disappears synchronously.
  setTimeout(() => URL.revokeObjectURL(url), 0)
}
