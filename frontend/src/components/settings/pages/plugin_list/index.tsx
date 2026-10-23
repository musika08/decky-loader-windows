import {
  DialogBody,
  DialogButton,
  DialogControlsSection,
  Focusable,
  GamepadEvent,
  Menu,
  MenuItem,
  NavEntryPositionPreferences,
  ReorderableEntry,
  ReorderableList,
  showContextMenu,
} from '@decky/ui';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { FaArrowsAltV, FaDownload, FaEllipsisH, FaRecycle } from 'react-icons/fa';

import { InstallType, enablePlugin } from '../../../../plugin';
import {
  StorePluginVersion,
  getPluginList,
  requestMultiplePluginInstalls,
  requestPluginInstall,
} from '../../../../store';
import { useSetting } from '../../../../utils/hooks/useSetting';
import { getPluginDisplayName } from '../../../../utils/pluginHelpers';
import { useDeckyState } from '../../../DeckyState';
import { moveEntry } from './moveEntry';
import MoveModeList from './MoveModeList';
import PluginListLabel from './PluginListLabel';

async function reinstallPlugin(pluginName: string, currentVersion?: string) {
  const serverData = await getPluginList();
  const remotePlugin = serverData?.find((x) => x.name == pluginName);
  if (remotePlugin && remotePlugin.versions?.length > 0) {
    const currentVersionData = remotePlugin.versions.find((version) => version.name == currentVersion);
    if (currentVersionData) requestPluginInstall(pluginName, currentVersionData, InstallType.REINSTALL);
  }
}

type PluginTableData = PluginData & {
  name: string;
  disabled: boolean;
  frozen: boolean;
  onFreeze(): void;
  onUnfreeze(): void;
  hidden: boolean;
  onHide(): void;
  onShow(): void;
  isDeveloper: boolean;
  // "Tap to move": start move mode for this plugin (disabled while sorting alphabetically).
  onStartMove(): void;
  moveDisabled: boolean;
  // Set once to put focus back on this row's Move button after leaving move mode.
  focusMoveButton: boolean;
  onMoveButtonFocused(): void;
};

const reloadPluginBackend = DeckyBackend.callable<[pluginName: string], void>('loader/reload_plugin');

function PluginInteractables(props: { entry: ReorderableEntry<PluginTableData> }) {
  const { t } = useTranslation();
  const moveButtonRef = useRef<HTMLDivElement>(null);
  const focusMoveButton = props.entry.data?.focusMoveButton ?? false;

  useEffect(() => {
    if (focusMoveButton) {
      moveButtonRef.current?.focus();
      props.entry.data?.onMoveButtonFocused();
    }
  }, [focusMoveButton]);

  // nothing to display without this data...
  if (!props.entry.data) {
    return null;
  }

  const {
    name,
    update,
    version,
    onHide,
    onShow,
    hidden,
    onFreeze,
    onUnfreeze,
    frozen,
    isDeveloper,
    disabled,
    onStartMove,
    moveDisabled,
  } = props.entry.data;

  const startMove = (e?: Event) => {
    // Keep a mouse click from also toggling the list's own X/D-pad reorder mode.
    e?.stopPropagation();
    if (!moveDisabled) onStartMove();
  };

  const showCtxMenu = (e: MouseEvent | GamepadEvent) => {
    showContextMenu(
      <Menu label={t('PluginListIndex.plugin_actions')}>
        {!disabled && (
          <MenuItem
            onSelected={async () => {
              try {
                await reloadPluginBackend(name);
              } catch (err) {
                console.error(`Error Reloading Plugin Backend for ${getPluginDisplayName(name, version)}`, err);
              }
            }}
          >
            {t('PluginListIndex.reload')}
          </MenuItem>
        )}
        <MenuItem
          onSelected={() =>
            DeckyPluginLoader.uninstallPlugin(
              name,
              t('PluginLoader.plugin_uninstall.title', { name }),
              t('PluginLoader.plugin_uninstall.button'),
              t('PluginLoader.plugin_uninstall.desc', { name }),
            )
          }
        >
          {t('PluginListIndex.uninstall')}
        </MenuItem>
        {disabled ? (
          <MenuItem onSelected={() => enablePlugin(name)}>{t('PluginListIndex.enable')}</MenuItem>
        ) : (
          <MenuItem
            onSelected={() =>
              DeckyPluginLoader.disablePlugin(
                name,
                t('PluginLoader.plugin_disable.title', { name }),
                t('PluginLoader.plugin_disable.button'),
                t('PluginLoader.plugin_disable.desc', { name }),
              )
            }
          >
            {t('PluginListIndex.disable')}
          </MenuItem>
        )}
        {!disabled &&
          (hidden ? (
            <MenuItem onSelected={onShow}>{t('PluginListIndex.show')}</MenuItem>
          ) : (
            <MenuItem onSelected={onHide}>{t('PluginListIndex.hide')}</MenuItem>
          ))}
        {frozen ? (
          <MenuItem onSelected={onUnfreeze}>{t('PluginListIndex.unfreeze')}</MenuItem>
        ) : (
          isDeveloper && <MenuItem onSelected={onFreeze}>{t('PluginListIndex.freeze')}</MenuItem>
        )}
      </Menu>,
      e.currentTarget ?? window,
    );
  };

  return (
    <Focusable navEntryPreferPosition={NavEntryPositionPreferences.MAINTAIN_X} style={{ display: 'flex' }}>
      {update ? (
        <DialogButton
          style={{ height: '40px', minWidth: '60px', marginRight: '10px' }}
          onClick={() => requestPluginInstall(name, update, InstallType.UPDATE)}
          onOKButton={() => requestPluginInstall(name, update, InstallType.UPDATE)}
        >
          <div style={{ display: 'flex', minWidth: '180px', justifyContent: 'space-between', alignItems: 'center' }}>
            {t('PluginListIndex.update_to', { name: update.name })}
            <FaDownload style={{ paddingLeft: '1rem' }} />
          </div>
        </DialogButton>
      ) : (
        <DialogButton
          style={{ height: '40px', minWidth: '60px', marginRight: '10px' }}
          onClick={() => reinstallPlugin(name, version)}
          onOKButton={() => reinstallPlugin(name, version)}
        >
          <div style={{ display: 'flex', minWidth: '180px', justifyContent: 'space-between', alignItems: 'center' }}>
            {t('PluginListIndex.reinstall')}
            <FaRecycle style={{ paddingLeft: '1rem' }} />
          </div>
        </DialogButton>
      )}
      {/* Wrapper carries the hover text: DialogButton has no title prop, and disabled buttons get no hover events. */}
      <div
        title={moveDisabled ? t('PluginListIndex.move_disabled_sorted') : t('PluginListIndex.move')}
        style={{ display: 'flex' }}
      >
        <DialogButton
          ref={moveButtonRef}
          disabled={moveDisabled}
          style={{
            height: '40px',
            width: '40px',
            padding: '10px 12px',
            minWidth: '40px',
            marginRight: '10px',
            display: 'flex',
            flexDirection: 'column',
            justifyContent: 'center',
          }}
          onClick={startMove}
          onOKButton={startMove}
          onOKActionDescription={t('PluginListIndex.move')}
        >
          <FaArrowsAltV />
        </DialogButton>
      </div>
      <DialogButton
        style={{
          height: '40px',
          width: '40px',
          padding: '10px 12px',
          minWidth: '40px',
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'center',
        }}
        onClick={showCtxMenu}
        onOKButton={showCtxMenu}
      >
        <FaEllipsisH />
      </DialogButton>
    </Focusable>
  );
}

type PluginData = {
  update?: StorePluginVersion;
  version?: string;
};

export default function PluginList({ isDeveloper }: { isDeveloper: boolean }) {
  const {
    installedPlugins,
    disabledPlugins,
    updates,
    pluginOrder,
    setPluginOrder,
    frozenPlugins,
    hiddenPlugins,
    sortPlugins,
  } = useDeckyState();
  const [_, setPluginOrderSetting] = useSetting<string[]>(
    'pluginOrder',
    installedPlugins.map((plugin) => plugin.name),
  );
  const { t } = useTranslation();

  useEffect(() => {
    DeckyPluginLoader.checkPluginUpdates();
  }, [installedPlugins, frozenPlugins]);

  const [pluginEntries, setPluginEntries] = useState<ReorderableEntry<PluginTableData>[]>([]);
  // "Tap to move": the plugin being moved, and the plugin whose Move button gets focus back afterwards.
  const [movingName, setMovingName] = useState<string | null>(null);
  const [focusMoveName, setFocusMoveName] = useState<string | null>(null);
  const hiddenPluginsService = DeckyPluginLoader.hiddenPluginsService;
  const frozenPluginsService = DeckyPluginLoader.frozenPluginsService;

  const sortedPlugins = useMemo(() => {
    return sortPlugins
      ? [...installedPlugins].sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }))
      : installedPlugins;
  }, [installedPlugins, sortPlugins]);

  useEffect(() => {
    setPluginEntries(
      installedPlugins.map(({ name, version }) => {
        const frozen = frozenPlugins.includes(name);
        const hidden = hiddenPlugins.includes(name);

        return {
          label: (
            <PluginListLabel
              name={name}
              frozen={frozen}
              hidden={hidden}
              version={version}
              disabled={disabledPlugins.find((p) => p.name == name) !== undefined}
            />
          ),
          position: sortPlugins ? sortedPlugins.findIndex((p) => p.name === name) : pluginOrder.indexOf(name),
          data: {
            name,
            disabled: disabledPlugins.some((disabledPlugin) => disabledPlugin.name === name),
            frozen,
            hidden,
            isDeveloper,
            version,
            update: updates?.get(name),
            onFreeze: () => frozenPluginsService.update([...frozenPlugins, name]),
            onUnfreeze: () => frozenPluginsService.update(frozenPlugins.filter((pluginName) => name !== pluginName)),
            onHide: () => hiddenPluginsService.update([...hiddenPlugins, name]),
            onShow: () => hiddenPluginsService.update(hiddenPlugins.filter((pluginName) => name !== pluginName)),
            onStartMove: () => setMovingName(name),
            moveDisabled: sortPlugins,
            focusMoveButton: focusMoveName === name,
            onMoveButtonFocused: () => setFocusMoveName(null),
          },
        };
      }),
    );
  }, [
    installedPlugins,
    updates,
    hiddenPlugins,
    disabledPlugins,
    sortPlugins,
    sortedPlugins,
    pluginOrder,
    focusMoveName,
  ]);

  // Leave move mode if alphabetical sorting gets turned on meanwhile.
  useEffect(() => {
    if (sortPlugins) setMovingName(null);
  }, [sortPlugins]);

  // Same order the list shows (ReorderableList sorts its entries by position the same way).
  const orderedEntries = useMemo(() => [...pluginEntries].sort((a, b) => a.position - b.position), [pluginEntries]);
  const movingIndex = movingName ? orderedEntries.findIndex((entry) => entry.data?.name === movingName) : -1;

  if (installedPlugins.length === 0) {
    return (
      <div>
        <p>{t('PluginListIndex.no_plugin')}</p>
      </div>
    );
  }

  function saveOrder(newOrder: string[]) {
    console.log(newOrder);
    setPluginOrder(newOrder);
    setPluginOrderSetting(newOrder);
  }

  function onSave(entries: ReorderableEntry<PluginTableData>[]) {
    if (sortPlugins) {
      return;
    }
    saveOrder(entries.map((entry) => entry.data!.name));
  }

  function endMove() {
    setFocusMoveName(movingName);
    setMovingName(null);
  }

  function onMoveHere(toIndex: number) {
    if (!sortPlugins && movingName) {
      const currentOrder = orderedEntries.map((entry) => entry.data!.name);
      saveOrder(moveEntry(currentOrder, movingName, toIndex));
    }
    endMove();
  }

  return (
    <DialogBody>
      {updates && updates.size > 0 && (
        <DialogButton
          onClick={() =>
            requestMultiplePluginInstalls(
              [...updates.entries()].map(([plugin, selectedVer]) => ({
                installType: InstallType.UPDATE,
                plugin,
                selectedVer,
              })),
            )
          }
          style={{
            position: 'absolute',
            top: '57px',
            right: '2.8vw',
            width: 'auto',
            display: 'flex',
            alignItems: 'center',
          }}
        >
          {t('PluginListIndex.update_all', { count: updates.size })}
          <FaDownload style={{ paddingLeft: '1rem' }} />
        </DialogButton>
      )}
      <DialogControlsSection style={{ marginTop: 0 }}>
        {sortPlugins && (
          <div style={{ fontSize: '0.85em', color: '#8b929a', padding: '0 0 8px' }}>
            {t('PluginListIndex.move_disabled_sorted')}
          </div>
        )}
        {movingIndex >= 0 ? (
          <MoveModeList<PluginTableData>
            entries={orderedEntries}
            movingIndex={movingIndex}
            movingLabel={orderedEntries[movingIndex].label}
            onMove={onMoveHere}
            onCancel={endMove}
          />
        ) : (
          <ReorderableList<PluginTableData>
            entries={pluginEntries}
            onSave={onSave}
            interactables={PluginInteractables}
            disableReordering={sortPlugins}
          />
        )}
      </DialogControlsSection>
    </DialogBody>
  );
}
