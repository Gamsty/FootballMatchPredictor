/*
TeamSelector Component

Reusable dropdown for selecting a football team.
Fetches teams from the API on mount and displays them in a <select>.

Props:
    - label: Display label above the dropdown (e.g. "Home Team")
    - value: Currently selected team ID (controlled component)
    - onChange: Callback when selection changes, receives team ID as number
    - excludeId: Team ID to hide from the list (prevents selecting same team twice)
    - icon: Emoji icon shown next to label (defaults to football)
*/

import { useEffect, useState } from "react";
import footballAPI from "../services/api";
import { useToast } from "../contexts/ToastContext";

function TeamSelector({ label, value, onChange, excludeId, icon = '⚽' }) {
    const [teams, setTeams] = useState([]);
    const [loading, setLoading] = useState(true);
    const { addToast } = useToast();

    // Fetch teams from API on component mount
    useEffect(() => {
        loadTeams();
    }, []);

    const loadTeams = async () => {
        try {
            setLoading(true);
            const data = await footballAPI.getTeams();
            setTeams(data);
        } catch (err) {
            addToast('Failed to load teams', 'error');
        } finally {
            setLoading(false);
        }
    };

    // Filter out the excluded team (e.g. if Arsenal is home, hide it from away dropdown)
    const filteredTeams = excludeId
        ? teams.filter((team) => team.id !== excludeId)
        : teams;

    // Show disabled dropdown while loading
    if (loading) {
        return (
            <div className="flex flex-col gap-2 flex-1">
                <label className="text-sm font-semibold text-gray-300">
                    {label}
                </label>
                <select
                    disabled
                    className="px-4 py-3 text-base border-2 border-gray-600 rounded-lg bg-gray-700 text-gray-400 cursor-not-allowed"
                >
                    <option>
                        Loading teams...
                    </option>
                </select>
            </div>
        );
    }

    return (
        <div className="flex flex-col gap-2 flex-1">
            <label className="text-sm font-semibold text-gray-300 flex items-center gap-2">
                <span>{icon}</span>
                <span>{label}</span>
            </label>
            <select
                value={value}
                onChange={(e) => onChange(Number(e.target.value))}
                className="px-4 py-3 text-base border-2 border-gray-600 rounded-lg bg-gray-800 text-gray-100
                            cursor-pointer transition-all duration-300 hover:border-primary-500
                            focus:border-primary-500 focus:ring-4 focus:ring-primary-800"
            >
                <option value="">Select team...</option>
                {filteredTeams.map((team) => (
                    <option
                        key={team.id}
                        value={team.id}
                    >
                        {team.name}
                    </option>
                ))}
            </select>
        </div>
    );
}

export default TeamSelector;
