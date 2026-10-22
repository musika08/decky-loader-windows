import { FC, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

// Shown in the Decky tab when the loader started in safe mode (plugins are not loaded).
// The route is public and read once when the tab mounts.
const SafeModeBanner: FC = () => {
  const { t } = useTranslation();
  const [safeMode, setSafeMode] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetch('http://127.0.0.1:1337/status/safe-mode', { cache: 'no-store' })
      .then((r) => r.json())
      .then((body) => {
        if (!cancelled && body?.safe_mode === true) setSafeMode(true);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  if (!safeMode) return null;
  return (
    <div
      style={{
        margin: '8px 16px 0',
        padding: '8px 12px',
        borderRadius: '4px',
        background: '#f5c518',
        color: '#1a1a1a',
        fontSize: '0.85rem',
        fontWeight: 'bold',
      }}
    >
      {t('SafeMode.banner')}
    </div>
  );
};

export default SafeModeBanner;
