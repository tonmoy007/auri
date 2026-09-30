import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { PriestKillSwitch } from '@/components/PriestKillSwitch'

describe('PriestKillSwitch', () => {
  it('says the app hides the Guide within about a minute, not straight away', () => {
    // Arrange
    render(<PriestKillSwitch enabled disabled={false} onChange={vi.fn()} />)

    // Act
    fireEvent.click(screen.getByRole('switch'))

    // Assert — the status response is cached for a minute, so "straight away" is false
    expect(screen.getByText(/within about a minute/i)).toBeTruthy()
    expect(screen.queryByText(/straight away/i)).toBeNull()
  })

  it('only asks on a click and changes nothing until confirmed', () => {
    // Arrange
    const onChange = vi.fn()
    render(<PriestKillSwitch enabled disabled={false} onChange={onChange} />)

    // Act
    fireEvent.click(screen.getByRole('switch'))

    // Assert
    expect(onChange).not.toHaveBeenCalled()
  })
})
