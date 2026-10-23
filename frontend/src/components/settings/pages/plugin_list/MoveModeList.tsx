import { DialogButton, Field, Focusable, ReorderableEntry } from '@decky/ui';
import { Fragment, ReactNode, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { FaArrowsAltV } from 'react-icons/fa';

import { moveTargets } from './moveEntry';

interface MoveModeListProps<T> {
  // Entries in their displayed order.
  entries: ReorderableEntry<T>[];
  movingIndex: number;
  movingLabel: ReactNode;
  // Called with the gap index (0 = top, entries.length = bottom).
  onMove(toIndex: number): void;
  onCancel(): void;
}

const targetStyle = {
  minHeight: '0px',
  height: '32px',
  padding: '0px 12px',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  gap: '8px',
  fontSize: '0.85em',
  border: '1px dashed rgba(255, 255, 255, 0.35)',
};

/**
 * "Tap to move" mode for the plugin list: the moving plugin's row is highlighted and
 * "Move here" targets sit between the rows. B or Cancel leaves the order unchanged.
 */
export default function MoveModeList<T>({ entries, movingIndex, movingLabel, onMove, onCancel }: MoveModeListProps<T>) {
  const { t } = useTranslation();
  const targets = new Set(moveTargets(entries.length, movingIndex));
  // Start on the nearest target: one place up if possible, otherwise one place down.
  const firstFocus = targets.has(movingIndex - 1) ? movingIndex - 1 : movingIndex + 2;
  const firstFocusRef = useRef<HTMLDivElement>(null);
  const cancelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    (firstFocusRef.current ?? cancelRef.current)?.focus();
  }, []);

  const renderTarget = (gap: number) =>
    targets.has(gap) ? (
      <DialogButton
        ref={gap === firstFocus ? firstFocusRef : undefined}
        preferredFocus={gap === firstFocus}
        style={targetStyle}
        onClick={() => onMove(gap)}
        onOKButton={() => onMove(gap)}
        onOKActionDescription={t('PluginListIndex.move_here')}
      >
        <FaArrowsAltV />
        {t('PluginListIndex.move_here')}
      </DialogButton>
    ) : null;

  return (
    <Focusable
      style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}
      onCancelButton={onCancel}
      onCancelActionDescription={t('PluginListIndex.move_cancel')}
    >
      <Field label={t('PluginListIndex.move_moving')} description={movingLabel} focusable={false}>
        <DialogButton
          ref={cancelRef}
          style={{ height: '40px', minWidth: '120px' }}
          onClick={onCancel}
          onOKButton={onCancel}
        >
          {t('PluginListIndex.move_cancel')}
        </DialogButton>
      </Field>
      {entries.map((entry, index) => (
        <Fragment key={index}>
          {renderTarget(index)}
          <div
            style={
              index === movingIndex
                ? {
                    background: 'rgba(26, 159, 255, 0.25)',
                    boxShadow: 'inset 4px 0 0 #1a9fff',
                    borderRadius: '4px',
                  }
                : { opacity: 0.6 }
            }
          >
            <Field label={entry.label} focusable={false} />
          </div>
        </Fragment>
      ))}
      {renderTarget(entries.length)}
    </Focusable>
  );
}
