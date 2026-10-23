import { callable, definePlugin } from '@decky/api';
import { PanelSection, PanelSectionRow, Spinner, staticClasses } from '@decky/ui';
import { useEffect, useState } from 'react';
import { FaBolt } from 'react-icons/fa';

type Support = { supported: boolean; product: string };

const getSupport = callable<[], Support>('get_support');

function Content() {
  const [support, setSupport] = useState<Support | null>(null);

  useEffect(() => {
    getSupport()
      .then(setSupport)
      .catch(() => setSupport({ supported: false, product: '' }));
  }, []);

  if (support === null) {
    return (
      <PanelSection title="Overdrive">
        <PanelSectionRow>
          <Spinner />
        </PanelSectionRow>
      </PanelSection>
    );
  }
  if (!support.supported) {
    return (
      <PanelSection title="Overdrive">
        <PanelSectionRow>Overdrive supports the ROG Ally family only</PanelSectionRow>
      </PanelSection>
    );
  }
  return (
    <PanelSection title="Overdrive">
      <PanelSectionRow>{support.product}</PanelSectionRow>
    </PanelSection>
  );
}

export default definePlugin(() => ({
  name: 'Overdrive',
  titleView: <div className={staticClasses.Title}>Overdrive</div>,
  content: <Content />,
  icon: <FaBolt />,
}));
