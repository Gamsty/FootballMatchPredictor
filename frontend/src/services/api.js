/* 
API Service
Handles all API calls to the backend
*/

import axios from "axios";

// Use environment variable, with fallback
const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:5000/api';

// Create axios instance
const api = axios.create({
    baseURL: API_BASE_URL,
    headers: {
        'Content-Type': 'application/json',
    },
});

// Response interceptor — logs API errors and passes them through
api.interceptors.response.use(
    (response) => response,
    (error) => {
        console.error('API Error:', error.response?.data || error.message);
        return Promise.reject(error);
    }
);

// API methods
export const footballAPI = {

    // Health check
    healthCheck: async () => {
        const response = await api.get('/health');
        return response.data;
    },

    // Teams
    getTeams: async () => {
        const response = await api.get('/teams');
        return response.data;
    },

    // Team details (stats, recent form)
    getTeam: async (teamId) => {
        const response = await api.get(`/teams/${teamId}`);
        return response.data;
    },

    // Predictions 
    predictMatch: async (homeTeamId, awayTeamId) => {
        const response = await api.post('/predict', {
            home_team_id: homeTeamId,
            away_team_id: awayTeamId,
        });
        return response.data;
    },

    // Prediction history with accuracy stats
    getPredictionHistory: async () => {
        const response = await api.get('/predictions/history');
        return response.data;
    },

    // Matches — supports filters: { season, team_id, status, limit }
    getMatches: async (params = {}) => {
        const response = await api.get('/matches', { params });
        return response.data;
    },

    getMatch: async (matchId) => {
        const response = await api.get(`/matches/${matchId}`);
        return response.data;
    },

    // Statistics — league-wide match/goal stats and model accuracy
    getStatisticsOverview: async () => {
        const response = await api.get('/statistics/overview');
        return response.data;
    },

    // Head-to-head record between two teams
    getHeadToHead: async (team1Id, team2Id) => {
        const response = await api.get('/statistics/head-to-head', {
            params: {
                team1_id: team1Id,
                team2_id: team2Id,
            },
        }); 
        return response.data;
    },
};

export default footballAPI;