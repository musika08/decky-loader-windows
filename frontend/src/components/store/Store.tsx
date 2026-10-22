import {
  Dropdown,
  DropdownOption,
  Focusable,
  PanelSectionRow,
  SteamSpinner,
  Tabs,
  TextField,
  ToggleField,
  findModule,
} from '@decky/ui';
import { Dispatch, FC, SetStateAction, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import logo from '../../../assets/plugin_store.png';
import Logger from '../../logger';
import { SortDirections, SortOptions, Store, StorePlugin, getPluginList, getStore } from '../../store';
import { useSetting } from '../../utils/hooks/useSetting';
import { useDeckyState } from '../DeckyState';
import ExternalLink from '../ExternalLink';
import PluginCard from './PluginCard';
import { getWindowsCompat, isWindowsPlatform, windowsCompatRank } from './WindowsCompatBadge';

const logger = new Logger('Store');

// Client-side sort by the Windows compatibility survey (Windows only); the store server has no such sort
const WINDOWS_COMPAT_SORT = 'windows_compat';
type StoreSortOption = SortOptions | typeof WINDOWS_COMPAT_SORT;
const windowsCompatSortData: [StoreSortOption, SortDirections] = [WINDOWS_COMPAT_SORT, SortDirections.ascending];

const StorePage: FC<{}> = () => {
  const [currentTabRoute, setCurrentTabRoute] = useState<string>('browse');
  const [pluginCount, setPluginCount] = useState<number | null>(null);
  const { TabCount } = findModule((m) => {
    if (m?.TabCount && m?.TabTitle) return true;
    return false;
  });

  const { t } = useTranslation();

  return (
    <>
      <div
        style={{
          marginTop: '40px',
          height: 'calc( 100% - 40px )',
          background: '#0005',
        }}
      >
        <Tabs
          activeTab={currentTabRoute}
          onShowTab={(tabId: string) => {
            setCurrentTabRoute(tabId);
          }}
          tabs={[
            {
              title: t('Store.store_tabs.title'),
              content: <BrowseTab setPluginCount={setPluginCount} />,
              id: 'browse',
              renderTabAddon: () => <span className={TabCount}>{pluginCount}</span>,
            },
            {
              title: t('Store.store_tabs.about'),
              content: <AboutTab />,
              id: 'about',
            },
          ]}
        />
      </div>
    </>
  );
};

const BrowseTab: FC<{ setPluginCount: Dispatch<SetStateAction<number | null>> }> = ({ setPluginCount }) => {
  const { t } = useTranslation();

  const dropdownSortOptions = useMemo(
    (): DropdownOption[] => [
      // ascending and descending order are the wrong way around for the alphabetical sort
      // this is because it was initially done incorrectly for i18n and 'fixing' it would
      // make all the translations incorrect
      { data: [SortOptions.name, SortDirections.ascending], label: t('Store.store_tabs.alph_desc') },
      { data: [SortOptions.name, SortDirections.descending], label: t('Store.store_tabs.alph_asce') },
      { data: [SortOptions.date, SortDirections.ascending], label: t('Store.store_tabs.date_asce') },
      { data: [SortOptions.date, SortDirections.descending], label: t('Store.store_tabs.date_desc') },
      { data: [SortOptions.downloads, SortDirections.descending], label: t('Store.store_tabs.downloads_desc') },
      { data: [SortOptions.downloads, SortDirections.ascending], label: t('Store.store_tabs.downloads_asce') },
    ],
    [],
  );

  const { installedPlugins, versionInfo } = useDeckyState();
  const isWindows = isWindowsPlatform(versionInfo?.platform);
  // Persisted in Decky's settings; hides tier D (Linux-only) plugins. Default on.
  const [hideWindowsIncompatible, setHideWindowsIncompatible] = useSetting<boolean>(
    'store.windows.hide_incompatible',
    true,
  );

  const sortOptions = useMemo(
    (): DropdownOption[] =>
      isWindows
        ? [...dropdownSortOptions, { data: windowsCompatSortData, label: t('Store.store_sort.windows_compat') }]
        : dropdownSortOptions,
    [isWindows, dropdownSortOptions],
  );

  // const filterOptions = useMemo((): DropdownOption[] => [{ data: 1, label: 'All' }], []);
  const [selectedSort, setSort] = useState<[StoreSortOption, SortDirections]>(dropdownSortOptions[0].data);
  // const [selectedFilter, setFilter] = useState<number>(filterOptions[0].data);
  const [searchFieldValue, setSearchValue] = useState<string>('');
  const [pluginList, setPluginList] = useState<StorePlugin[] | null>(null);
  const [isTesting, setIsTesting] = useState<boolean>(false);

  useEffect(() => {
    (async () => {
      const [sortBy, sortDirection] = selectedSort;
      let res: StorePlugin[];
      if (sortBy === WINDOWS_COMPAT_SORT) {
        // fetch A to Z, then stable-sort by tier so each tier stays alphabetical
        res = await getPluginList(SortOptions.name, SortDirections.ascending);
        res = res
          .map((plugin, index) => ({ plugin, index, rank: windowsCompatRank(plugin.name, plugin.id) }))
          .sort((a, b) => a.rank - b.rank || a.index - b.index)
          .map(({ plugin }) => plugin);
      } else {
        res = await getPluginList(sortBy, sortDirection);
      }
      logger.debug('got data!', res);
      setPluginList(res);
    })();
  }, [selectedSort]);

  const visiblePlugins = useMemo(
    () =>
      pluginList && isWindows && hideWindowsIncompatible
        ? pluginList.filter((plugin) => getWindowsCompat(plugin.name, plugin.id).tier !== 'D')
        : pluginList,
    [pluginList, isWindows, hideWindowsIncompatible],
  );

  useEffect(() => {
    if (visiblePlugins) setPluginCount(visiblePlugins.length);
  }, [visiblePlugins]);

  useEffect(() => {
    (async () => {
      const storeRes = await getStore();
      logger.debug(`store is ${storeRes}, isTesting is ${storeRes === Store.Testing}`);
      setIsTesting(storeRes === Store.Testing);
    })();
  }, []);

  return (
    <>
      <style>{`
              .deckyStoreCardInstallContainer > .Panel {
                padding: 0;
              }
            `}</style>
      {/* This should be used once filtering is added

      <PanelSectionRow>
        <Focusable style={{ display: 'flex', maxWidth: '100%' }}>
          <div
            style={{
              display: 'flex',
              flexDirection: 'column',
              width: '47.5%',
            }}
          >
            <span className="DialogLabel">{t("Store.store_sort.label")}</span>
            <Dropdown
              menuLabel={t("Store.store_sort.label") as string}
              rgOptions={dropdownSortOptions}
              strDefaultLabel={t("Store.store_sort.label_def") as string}
              selectedOption={selectedSort}
              onChange={(e) => setSort(e.data)}
            />
          </div>
          <div
            style={{
              display: 'flex',
              flexDirection: 'column',
              width: '47.5%',
              marginLeft: 'auto',
            }}
          >
            <span className="DialogLabel">{t("Store.store_filter.label")}</span>
            <Dropdown
              menuLabel={t("Store.store_filter.label")}
              rgOptions={filterOptions}
              strDefaultLabel={t("Store.store_filter.label_def")}
              selectedOption={selectedFilter}
              onChange={(e) => setFilter(e.data)}
            />
          </div>
        </Focusable>
      </PanelSectionRow>
      <div style={{ justifyContent: 'center', display: 'flex' }}>
        <Focusable style={{ display: 'flex', alignItems: 'center', width: '96%' }}>
          <div style={{ width: '100%' }}>
            <TextField label={t("Store.store_search.label")} value={searchFieldValue} onChange={(e) => setSearchValue(e.target.value)} />
          </div>
        </Focusable>
      </div>
      */}
      <PanelSectionRow>
        <Focusable style={{ display: 'flex', maxWidth: '100%' }}>
          <div
            style={{
              display: 'flex',
              flexDirection: 'column',
              minWidth: '100%',
              maxWidth: '100%',
            }}
          >
            <span className="DialogLabel">{t('Store.store_sort.label')}</span>
            <Dropdown
              menuLabel={t('Store.store_sort.label') as string}
              rgOptions={sortOptions}
              strDefaultLabel={t('Store.store_sort.label_def') as string}
              selectedOption={selectedSort}
              onChange={(e) => setSort(e.data)}
            />
          </div>
        </Focusable>
      </PanelSectionRow>
      {isWindows && (
        <PanelSectionRow>
          <ToggleField
            label={t('Store.store_filter.hide_windows_incompatible')}
            description={t('Store.store_filter.hide_windows_incompatible_desc')}
            checked={hideWindowsIncompatible}
            onChange={(checked) => setHideWindowsIncompatible(checked)}
          />
        </PanelSectionRow>
      )}
      <div style={{ justifyContent: 'center', display: 'flex' }}>
        <Focusable style={{ display: 'flex', alignItems: 'center', width: '100%' }}>
          <div style={{ width: '100%' }}>
            <TextField
              label={t('Store.store_search.label')}
              value={searchFieldValue}
              onChange={(e) => setSearchValue(e.target.value)}
            />
          </div>
        </Focusable>
      </div>
      {isTesting && (
        <div
          style={{
            alignItems: 'center',
            display: 'flex',
            flexDirection: 'column',
            marginLeft: '20px',
            marginRight: '20px',
            marginBottom: '20px',
            padding: '8px 36px',
            background: 'rgba(255, 255, 0, 0.067)',
            textAlign: 'center',
            border: '2px solid rgba(255, 255, 0, 0.467)',
          }}
        >
          <h2 style={{ margin: 0 }}>{t('Store.store_testing_warning.label')}</h2>
          <span>
            {`${t('Store.store_testing_warning.desc')} `}
            <ExternalLink
              href="https://decky.xyz/testing"
              target="_blank"
              style={{
                textDecoration: 'none',
              }}
            >
              decky.xyz/testing
            </ExternalLink>
          </span>
        </div>
      )}
      <div>
        {!visiblePlugins ? (
          <div style={{ height: '100%' }}>
            <SteamSpinner background="transparent" />
          </div>
        ) : (
          visiblePlugins
            .filter((plugin: StorePlugin) => {
              return (
                plugin.name.toLowerCase().includes(searchFieldValue.toLowerCase()) ||
                plugin.description.toLowerCase().includes(searchFieldValue.toLowerCase()) ||
                plugin.author.toLowerCase().includes(searchFieldValue.toLowerCase()) ||
                plugin.tags.some((tag: string) => tag.toLowerCase().includes(searchFieldValue.toLowerCase()))
              );
            })
            .map((plugin: StorePlugin) => (
              <PluginCard
                key={`${plugin.id}_${plugin.name}`}
                storePlugin={plugin}
                installedPlugin={installedPlugins.find((installedPlugin) => installedPlugin.name === plugin.name)}
              />
            ))
        )}
      </div>
    </>
  );
};

const AboutTab: FC<{}> = () => {
  const { t } = useTranslation();

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      <style>{`
              .deckyStoreAboutHeader {
                font-size: 24px;
                font-weight: 600;
                margin-top: 20px;
              }
            `}</style>
      <img
        src={logo}
        style={{
          width: '256px',
          height: 'auto',
          alignSelf: 'center',
        }}
      />
      <span className="deckyStoreAboutHeader">Testing</span>
      <span>
        {t('Store.store_testing_cta')}{' '}
        <ExternalLink
          href="https://decky.xyz/testing"
          target="_blank"
          style={{
            textDecoration: 'none',
          }}
        >
          decky.xyz/testing
        </ExternalLink>
      </span>
      <span className="deckyStoreAboutHeader">{t('Store.store_contrib.label')}</span>
      <span>{t('Store.store_contrib.desc')}</span>
      <span className="deckyStoreAboutHeader">{t('Store.store_source.label')}</span>
      <span>{t('Store.store_source.desc')}</span>
    </div>
  );
};

export default StorePage;
