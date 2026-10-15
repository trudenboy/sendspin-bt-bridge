/**
 * Russian plural forms for pipe-separated messages ("один | два–четыре | пять"):
 * 1, 21, 101 → first; 2–4, 22–24 → second; 0, 5–20, 11–14, 25… → third.
 * With two forms it falls back to one/other.
 */
export function russianPlural(choice: number, choicesLength: number): number {
  const n = Math.abs(choice)
  if (choicesLength < 3) return n === 1 ? 0 : 1
  const mod10 = n % 10
  const mod100 = n % 100
  if (mod10 === 1 && mod100 !== 11) return 0
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return 1
  return 2
}
