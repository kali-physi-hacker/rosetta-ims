// Inline loading spinner — inherits the button's text colour via currentColor by
// default. Relies on the `ims-spin` keyframe defined in globals.css.
export function Spinner({ size = 12, color = 'currentColor' }: { size?: number; color?: string }) {
  return (
    <span
      aria-hidden
      style={{
        display: 'inline-block', width: size, height: size,
        border: `2px solid ${color}`, borderTopColor: 'transparent',
        borderRadius: '50%', animation: 'ims-spin 0.6s linear infinite',
        verticalAlign: '-2px', flexShrink: 0,
      }}
    />
  )
}
