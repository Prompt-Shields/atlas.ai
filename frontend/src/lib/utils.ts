import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

/**
 * Merge Tailwind class names with proper precedence + conditional support.
 *
 * Used by every shadcn UI primitive in `frontend/src/components/ui/`.
 * Combines `clsx` (conditional className builder) with `tailwind-merge`
 * (resolves conflicting Tailwind classes — last wins).
 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
