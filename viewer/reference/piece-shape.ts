import type { PieceDetail, SessionPiece } from '@/lib/types'

export function pieceDetailToSessionPiece(detail: PieceDetail): SessionPiece {
  return {
    id: detail.piece.id,
    label: detail.piece.label,
    start_ms: detail.piece.start_ms,
    end_ms: detail.piece.end_ms,
    duration_s: detail.piece.duration_s,
    stroke_count: detail.piece.stroke_count,
    avg_stroke_rate: detail.piece.avg_stroke_rate,
    distance_m: detail.piece.distance_m,
    avg_speed_ms: detail.piece.avg_speed_ms,
    split_500m: detail.piece.split_500m,
    dist_per_stroke_m: detail.piece.dist_per_stroke_m,
    use_for_cp: detail.piece.use_for_cp,
    notes: detail.piece.notes,
    tags: detail.piece.tags,
    force_offsets: detail.piece.force_offsets,
    force_offsets_updated_at: detail.piece.force_offsets_updated_at,
    curves_path: detail.piece.curves_path,
    created_at: detail.piece.created_at,
    rowers: detail.rowers,
    periodic_snapshots: detail.periodic_snapshots,
    aperiodic_averages: detail.aperiodic_averages,
    _external: {
      session_id: detail.piece.session_id,
      session_name: detail.piece.training_sessions.session_name,
      session_date: detail.piece.training_sessions.session_date,
      boat_class: detail.piece.training_sessions.boat_class,
      is_sculling: detail.piece.training_sessions.is_sculling,
    },
  }
}
