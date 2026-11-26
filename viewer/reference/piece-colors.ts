export const PIECE_COLORS = [
  '#00b4d8',
  '#ff922b',
  '#cc5de8',
  '#51cf66',
  '#ffd43b',
  '#f06595',
  '#0077b6',
]

export function pieceColor(index: number): string {
  return PIECE_COLORS[Math.abs(index) % PIECE_COLORS.length]
}

export function pieceColorById(pieces: { id: string }[], pieceId: string): string {
  const index = pieces.findIndex(piece => piece.id === pieceId)
  return pieceColor(index >= 0 ? index : 0)
}
