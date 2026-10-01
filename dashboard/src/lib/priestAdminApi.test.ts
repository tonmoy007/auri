import { describe, expect, it, vi } from 'vitest'
import { adminApi, priestAdminApi, type Requester } from '@/lib/api'

function recorder() {
  const request = vi.fn().mockResolvedValue({}) as unknown as Requester & ReturnType<typeof vi.fn>
  return request
}

describe('priestAdminApi', () => {
  it('reads each admin view from its own GET endpoint', async () => {
    // Arrange
    const request = recorder()

    // Act
    await priestAdminApi.getIndex(request)
    await priestAdminApi.getReindexStatus(request)
    await priestAdminApi.getHealth(request)
    await priestAdminApi.getUsage(request)
    await priestAdminApi.getReport(request)

    // Assert
    expect(request.mock.calls.map((call) => call[0])).toEqual([
      '/admin/priest/index',
      '/admin/priest/reindex/status',
      '/admin/priest/health',
      '/admin/priest/usage',
      '/admin/priest/report',
    ])
    expect(request.mock.calls.every((call) => call[1] === undefined)).toBe(true)
  })

  it('starts a reindex with a POST and no body', async () => {
    // Arrange
    const request = recorder()

    // Act
    await priestAdminApi.startReindex(request)

    // Assert
    expect(request).toHaveBeenCalledWith('/admin/priest/reindex', { method: 'POST' })
  })

  it('activates a version by posting its name', async () => {
    // Arrange
    const request = recorder()

    // Act
    await priestAdminApi.activate(request, 'v2')

    // Assert
    expect(request).toHaveBeenCalledWith('/admin/priest/activate', {
      method: 'POST',
      body: JSON.stringify({ version: 'v2' }),
    })
  })
})

describe('kill switch through the config endpoint', () => {
  it('sets PRIEST_MODE_ENABLED with a PUT to /admin/config', async () => {
    // Arrange
    const request = recorder()

    // Act
    await adminApi.setConfig(request, 'PRIEST_MODE_ENABLED', 'true')

    // Assert
    expect(request).toHaveBeenCalledWith('/admin/config', {
      method: 'PUT',
      body: JSON.stringify({ key: 'PRIEST_MODE_ENABLED', value: 'true' }),
    })
  })
})
