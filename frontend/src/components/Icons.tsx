type IconProps = { size?: number; className?: string };

function svgProps(size = 18) {
  return {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.7,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };
}

export function IconOverview({ size, className }: IconProps) {
  return (
    <svg {...svgProps(size)} className={className}>
      <rect x="3" y="3" width="7" height="7" rx="1.6" />
      <rect x="14" y="3" width="7" height="7" rx="1.6" />
      <rect x="3" y="14" width="7" height="7" rx="1.6" />
      <rect x="14" y="14" width="7" height="7" rx="1.6" />
    </svg>
  );
}

export function IconStrategy({ size, className }: IconProps) {
  return (
    <svg {...svgProps(size)} className={className}>
      <circle cx="12" cy="12" r="8.5" />
      <circle cx="12" cy="12" r="3.2" />
      <path d="M12 3.5v3M12 17.5v3M3.5 12h3M17.5 12h3" />
    </svg>
  );
}

export function IconTrading({ size, className }: IconProps) {
  return (
    <svg {...svgProps(size)} className={className}>
      <path d="M4 7h11l-2.5-2.5M20 17H9l2.5 2.5" />
      <path d="M17 4.5 19.5 7 17 9.5M7 14.5 4.5 17 7 19.5" />
    </svg>
  );
}

export function IconAnalytics({ size, className }: IconProps) {
  return (
    <svg {...svgProps(size)} className={className}>
      <path d="M4 20V4" />
      <path d="M4 20h16" />
      <path d="M8 16v-3M12 16V8M16 16v-6M20 16v-2" />
    </svg>
  );
}

export function IconSettings({ size, className }: IconProps) {
  return (
    <svg {...svgProps(size)} className={className}>
      <path d="M4 7h9M17 7h3M4 12h3M11 12h9M4 17h9M17 17h3" />
      <circle cx="15" cy="7" r="1.8" />
      <circle cx="9" cy="12" r="1.8" />
      <circle cx="15" cy="17" r="1.8" />
    </svg>
  );
}

export function IconLogs({ size, className }: IconProps) {
  return (
    <svg {...svgProps(size)} className={className}>
      <rect x="3" y="4" width="18" height="16" rx="2.4" />
      <path d="m7 9 2.5 2.5L7 14M12.5 14H17" />
    </svg>
  );
}

export function IconPause({ size, className }: IconProps) {
  return (
    <svg {...svgProps(size)} className={className}>
      <rect x="7" y="5" width="3.4" height="14" rx="1.2" />
      <rect x="13.6" y="5" width="3.4" height="14" rx="1.2" />
    </svg>
  );
}

export function IconPlay({ size, className }: IconProps) {
  return (
    <svg {...svgProps(size)} className={className}>
      <path d="M7 4.8v14.4L19 12 7 4.8Z" />
    </svg>
  );
}

export function IconShield({ size, className }: IconProps) {
  return (
    <svg {...svgProps(size)} className={className}>
      <path d="M12 3 4.8 5.8v5.1c0 4.6 3 8.1 7.2 10.1 4.2-2 7.2-5.5 7.2-10.1V5.8L12 3Z" />
      <path d="m9 11.8 2.1 2.1 3.9-4" />
    </svg>
  );
}

export function IconWifi({ size, className }: IconProps) {
  return (
    <svg {...svgProps(size)} className={className}>
      <path d="M3.5 8.8a13 13 0 0 1 17 0M6.5 12a9 9 0 0 1 11 0M9.4 15.1a5 5 0 0 1 5.2 0" />
      <circle cx="12" cy="18.4" r="1.2" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function IconWifiOff({ size, className }: IconProps) {
  return (
    <svg {...svgProps(size)} className={className}>
      <path d="M3.5 8.8a13 13 0 0 1 6.3-3.1M14.5 5.7a13 13 0 0 1 6 3.1M6.5 12a9 9 0 0 1 3.1-1.8M14.6 10.3a9 9 0 0 1 2.9 1.7M9.4 15.1a5 5 0 0 1 3.2-.5" />
      <path d="m3 3 18 18" />
      <circle cx="12" cy="18.4" r="1.2" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function IconArrowUp({ size, className }: IconProps) {
  return (
    <svg {...svgProps(size)} className={className}>
      <path d="M7 17 17 7M9 7h8v8" />
    </svg>
  );
}

export function IconArrowDown({ size, className }: IconProps) {
  return (
    <svg {...svgProps(size)} className={className}>
      <path d="M7 7l10 10M17 9v8H9" />
    </svg>
  );
}

export function IconAlert({ size, className }: IconProps) {
  return (
    <svg {...svgProps(size)} className={className}>
      <path d="M12 4 2.8 20h18.4L12 4Z" />
      <path d="M12 10v4.2" />
      <circle cx="12" cy="17.2" r="0.9" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function IconCheck({ size, className }: IconProps) {
  return (
    <svg {...svgProps(size)} className={className}>
      <path d="m4.5 12.5 5 5L20 6.5" />
    </svg>
  );
}

export function IconClose({ size, className }: IconProps) {
  return (
    <svg {...svgProps(size)} className={className}>
      <path d="M6 6l12 12M18 6 6 18" />
    </svg>
  );
}

export function IconClock({ size, className }: IconProps) {
  return (
    <svg {...svgProps(size)} className={className}>
      <circle cx="12" cy="12" r="8.6" />
      <path d="M12 7.4V12l3 1.8" />
    </svg>
  );
}

export function IconZap({ size, className }: IconProps) {
  return (
    <svg {...svgProps(size)} className={className}>
      <path d="M13.2 2.5 5 13.2h5.6L10.2 21.5 19 10.6h-5.7l-.1-8.1Z" />
    </svg>
  );
}

export function IconRefresh({ size, className }: IconProps) {
  return (
    <svg {...svgProps(size)} className={className}>
      <path d="M20 7v4.5h-4.5M4 17v-4.5h4.5" />
      <path d="M18.2 11.5A7 7 0 0 0 6.4 7.2M5.8 12.5a7 7 0 0 0 11.8 4.3" />
    </svg>
  );
}

export function IconLock({ size, className }: IconProps) {
  return (
    <svg {...svgProps(size)} className={className}>
      <rect x="5" y="10" width="14" height="10" rx="2.2" />
      <path d="M8 10V7.5a4 4 0 0 1 8 0V10" />
    </svg>
  );
}

export function IconActivity({ size, className }: IconProps) {
  return (
    <svg {...svgProps(size)} className={className}>
      <path d="M2.8 12h4l2.4-6.2L14 18l2.4-6h4.8" />
    </svg>
  );
}

export function IconTrash({ size, className }: IconProps) {
  return (
    <svg {...svgProps(size)} className={className}>
      <path d="M4 6.5h16M9 6.5V4.8h6v1.7M6.4 6.5l.9 12.7h9.4l.9-12.7" />
      <path d="M10 10.5v5M14 10.5v5" />
    </svg>
  );
}

export function IconSearch({ size, className }: IconProps) {
  return (
    <svg {...svgProps(size)} className={className}>
      <circle cx="10.8" cy="10.8" r="6.4" />
      <path d="m15.6 15.6 4.4 4.4" />
    </svg>
  );
}

export function IconWallet({ size, className }: IconProps) {
  return (
    <svg {...svgProps(size)} className={className}>
      <rect x="3.2" y="6" width="17.6" height="13" rx="2.6" />
      <path d="M3.2 9.6h17.6M16.5 14.2h1.8" />
    </svg>
  );
}

export function IconTarget({ size, className }: IconProps) {
  return (
    <svg {...svgProps(size)} className={className}>
      <circle cx="12" cy="12" r="8.5" />
      <circle cx="12" cy="12" r="4.7" />
      <circle cx="12" cy="12" r="1.2" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function IconLayers({ size, className }: IconProps) {
  return (
    <svg {...svgProps(size)} className={className}>
      <path d="m12 3.2 8.4 4.4-8.4 4.4-8.4-4.4L12 3.2Z" />
      <path d="m4.4 12.2 7.6 4 7.6-4M4.4 16.4l7.6 4 7.6-4" />
    </svg>
  );
}

export function IconUser({ size, className }: IconProps) {
  return (
    <svg {...svgProps(size)} className={className}>
      <circle cx="12" cy="8" r="3.6" />
      <path d="M4.5 20a7.5 7.5 0 0 1 15 0" />
    </svg>
  );
}

export function IconEye({ size, className }: IconProps) {
  return (
    <svg {...svgProps(size)} className={className}>
      <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12Z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

export function IconEyeOff({ size, className }: IconProps) {
  return (
    <svg {...svgProps(size)} className={className}>
      <path d="M3 3l18 18" />
      <path d="M10.6 5.1A9.9 9.9 0 0 1 12 5c6.4 0 10 7 10 7a17 17 0 0 1-3.2 4M6.3 6.4C3.6 8.2 2 12 2 12s3.6 7 10 7c1.6 0 3-.4 4.2-1" />
      <path d="M9.9 9.9a3 3 0 0 0 4.2 4.2" />
    </svg>
  );
}

export function IconLogout({ size, className }: IconProps) {
  return (
    <svg {...svgProps(size)} className={className}>
      <path d="M9 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h3" />
      <path d="M16 8l4 4-4 4M20 12H9" />
    </svg>
  );
}
