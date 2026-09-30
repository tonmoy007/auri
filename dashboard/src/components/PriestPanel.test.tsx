import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { PriestPanel } from '@/components/PriestPanel'

vi.mock('@/components/PriestStatusCard', () => ({ PriestStatusCard: () => <p>status card</p> }))
vi.mock('@/components/PriestIndexCard', () => ({ PriestIndexCard: () => <p>index card</p> }))
vi.mock('@/components/PriestEvalCard', () => ({ PriestEvalCard: () => <p>eval card</p> }))
vi.mock('@/components/PriestUsageCard', () => ({ PriestUsageCard: () => <p>usage card</p> }))

describe('PriestPanel', () => {
  it('shows status, index, evaluation and usage, in that order', () => {
    // Arrange / Act
    const { container } = render(<PriestPanel />)

    // Assert
    const order = Array.from(container.querySelectorAll('p')).map((p) => p.textContent)
    expect(order).toEqual(['status card', 'index card', 'eval card', 'usage card'])
    expect(screen.getByText('status card')).toBeTruthy()
  })
})
