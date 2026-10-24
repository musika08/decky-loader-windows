using System;
using System.Collections.Generic;

namespace Ally.Shared
{
    // The Armoury Crate commands (armoury_state, armoury_stop, armoury_restore), shared by both helpers.
    // Only names in ArmouryServices (G-Helper AsusService.cs services + servicesAC) are ever touched.
    public class ArmouryCommands
    {
        private readonly IServices services;
        private readonly Action<int> sleep;

        // sleep is injectable like the protocol core's, so tests record delays instead of waiting.
        public ArmouryCommands(IServices services, Action<int> sleep)
        {
            this.services = services;
            this.sleep = sleep ?? System.Threading.Thread.Sleep;
        }

        public string State(object id)
        {
            Dictionary<string, string> types = services.StartTypes(ArmouryServices.All);
            List<string> running = services.Running(ArmouryServices.All);
            bool installed = false;
            foreach (string m in ArmouryServices.InstalledMarkers) if (types.ContainsKey(m)) installed = true;
            var d = new Dictionary<string, object>();
            d["installed"] = installed;
            d["startTypes"] = Ordered(types);
            d["running"] = running;
            return ProtocolKit.Reply(id, true, "", d);
        }

        // G-Helper AsusService.StopAsusServices: each running listed service is stopped and set to Manual (servicesAC)
        // or Disabled (services); services that are not running are left alone. The start types and running set are
        // read before the first change and returned, so the caller can store them for armoury_restore.
        // A failure rolls every service touched so far back to the recorded state.
        public string Stop(object id)
        {
            Dictionary<string, string> types = services.StartTypes(ArmouryServices.All);
            List<string> running = services.Running(ArmouryServices.All);

            var steps = new List<ServiceStep>();
            foreach (string name in ArmouryServices.All)
                if (running.Contains(name)) steps.Add(new ServiceStep(name, ServiceAction.Stop, ArmouryServices.StopStartType(name)));

            var data = new Dictionary<string, object>();
            data["startTypes"] = Ordered(types);
            data["running"] = running;
            string err;
            if (!ApplyServiceSteps(steps, types, running, "stop", data, out err)) return ProtocolKit.Reply(id, false, err, data);
            return ProtocolKit.Reply(id, true, "", data);
        }

        // Puts back exactly the given start types and starts the given running services, in list order. Services that
        // are no longer installed are skipped (reported in data.skipped). Nothing else is stopped or changed.
        // A failure rolls every service touched so far back to how it was before this call.
        public string Restore(object id, Dictionary<string, object> args)
        {
            object raw;
            if (!args.TryGetValue("recorded", out raw) || !(raw is Dictionary<string, object>))
                return ProtocolKit.Reply(id, false, "Refused: 'recorded' must be an object of service name to start type.", null);
            var recorded = new Dictionary<string, string>();
            foreach (KeyValuePair<string, object> kv in (Dictionary<string, object>)raw)
            {
                if (!(kv.Value is string)) return ProtocolKit.Reply(id, false, "Refused: start type for '" + kv.Key + "' must be a string.", null);
                if (!ArmouryServices.IsListed(kv.Key))
                    return ProtocolKit.Reply(id, false, "Refused: '" + kv.Key + "' is not one of the Armoury Crate services this plugin manages.", null);
                if (!ArmouryServices.IsStartType((string)kv.Value))
                    return ProtocolKit.Reply(id, false, "Refused: start type for '" + kv.Key + "' must be one of "
                        + string.Join(", ", ArmouryServices.StartTypeNames) + ".", null);
                recorded[kv.Key] = (string)kv.Value;
            }
            if (!args.TryGetValue("running", out raw) || !(raw is object[]))
                return ProtocolKit.Reply(id, false, "Refused: 'running' must be a list of service names.", null);
            var toStart = new List<string>();
            foreach (object o in (object[])raw)
            {
                string name = o as string;
                if (name == null) return ProtocolKit.Reply(id, false, "Refused: 'running' must contain only service names.", null);
                if (!ArmouryServices.IsListed(name))
                    return ProtocolKit.Reply(id, false, "Refused: '" + name + "' is not one of the Armoury Crate services this plugin manages.", null);
                if (!recorded.ContainsKey(name))
                    return ProtocolKit.Reply(id, false, "Refused: running service '" + name + "' has no recorded start type.", null);
                toStart.Add(name);
            }

            Dictionary<string, string> types = services.StartTypes(ArmouryServices.All);
            List<string> running = services.Running(ArmouryServices.All);

            // Two passes, so a service that depends on another listed service never starts while that one is still
            // Disabled: first every recorded start type (G-Helper list order), then the services that were running,
            // in the recorded order. A failure rolls back every step so far, as before.
            var steps = new List<ServiceStep>();
            var skipped = new List<string>();
            foreach (string name in ArmouryServices.All)
            {
                string type;
                if (!recorded.TryGetValue(name, out type)) continue;
                if (!types.ContainsKey(name)) { skipped.Add(name); continue; }
                steps.Add(new ServiceStep(name, ServiceAction.SetType, type));
            }
            foreach (string name in toStart)
            {
                if (!types.ContainsKey(name)) continue;
                steps.Add(new ServiceStep(name, ServiceAction.Start, recorded[name]));
            }

            var data = new Dictionary<string, object>();
            data["skipped"] = skipped;
            string err;
            if (!ApplyServiceSteps(steps, types, running, "restore", data, out err)) return ProtocolKit.Reply(id, false, err, data);
            return ProtocolKit.Reply(id, true, "", data);
        }

        private enum ServiceAction { Stop, Start, SetType }

        private class ServiceStep
        {
            public readonly string Name;
            public readonly ServiceAction Action;
            public readonly string StartType;
            public ServiceStep(string name, ServiceAction action, string startType) { Name = name; Action = action; StartType = startType; }
        }

        // Runs the steps in order. On the first failure, every step done so far (the failed one included, as it may
        // have half-happened) is undone in reverse: back to its start type in `before`, and running only if it was.
        // Only the listed services this call changed are put back: a dependent service that ServiceController.Stop
        // stopped alongside one of them is not restarted (same as G-Helper, whose Stop-Service -Force does the same).
        // Worst case duration: up to 30 s per stop/start (ServiceHardware.Wait), times the services changed, and the
        // same again for a rollback; dependents add their own stop time.
        // data gets "failed" and "rolledBack"; err names the service and lists any rollback that also failed.
        private bool ApplyServiceSteps(List<ServiceStep> steps, Dictionary<string, string> before, List<string> wasRunning,
            string what, Dictionary<string, object> data, out string err)
        {
            err = null;
            for (int i = 0; i < steps.Count; i++)
            {
                ServiceStep st = steps[i];
                try
                {
                    switch (st.Action)
                    {
                        case ServiceAction.Stop: services.Stop(st.Name, st.StartType); break;
                        case ServiceAction.Start: services.Start(st.Name, st.StartType); break;
                        default: services.SetStartType(st.Name, st.StartType); break;
                    }
                }
                catch (Exception ex)
                {
                    string verb = st.Action == ServiceAction.Stop ? "stop" : (st.Action == ServiceAction.Start ? "start" : "set the start type of");
                    var rollbackFailures = new List<string>();
                    for (int j = i; j >= 0; j--)
                    {
                        ServiceStep u = steps[j];
                        string type;
                        if (!before.TryGetValue(u.Name, out type)) continue;
                        try
                        {
                            if (wasRunning.Contains(u.Name)) services.Start(u.Name, type);
                            else if (u.Action == ServiceAction.Start) services.Stop(u.Name, type);
                            else services.SetStartType(u.Name, type);
                        }
                        catch (Exception rex) { rollbackFailures.Add(u.Name + " (" + rex.Message + ")"); }
                    }
                    data["failed"] = st.Name;
                    data["rolledBack"] = rollbackFailures.Count == 0;
                    err = "Could not " + verb + " " + st.Name + " (" + what + "): " + ex.Message.TrimEnd('.') + "."
                        + (rollbackFailures.Count == 0
                            ? " Every service changed so far was put back."
                            : " Rollback also failed for " + string.Join(", ", rollbackFailures) + ".");
                    return false;
                }
            }
            return true;
        }

        // Start types in ArmouryServices order, for stable replies.
        private static Dictionary<string, object> Ordered(Dictionary<string, string> types)
        {
            var d = new Dictionary<string, object>();
            foreach (string name in ArmouryServices.All)
            {
                string t;
                if (types.TryGetValue(name, out t)) d[name] = t;
            }
            return d;
        }
    }
}
