import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import type { StaffOverview } from '@/lib/api'

interface PrivacyStaffCardProps {
  staff: StaffOverview
}

/** How many active accounts hold each role, and the named list for administrators. */
export function PrivacyStaffCard({ staff }: PrivacyStaffCardProps) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Who can sign in</CardTitle>
        <CardDescription>
          Active staff accounts by role.
          {staff.members === null && ' Names are shown to administrators only.'}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <ul className="flex flex-wrap gap-x-6 gap-y-1 text-sm">
          {Object.entries(staff.role_counts).map(([role, count]) => (
            <li key={role}>
              <span className="capitalize">{role}</span>: {count}
            </li>
          ))}
        </ul>
        {staff.members !== null && (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Email</TableHead>
                <TableHead>Role</TableHead>
                <TableHead>Last sign-in</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {staff.members.map((member) => (
                <TableRow key={member.email}>
                  <TableCell>{member.email}</TableCell>
                  <TableCell className="capitalize">{member.role}</TableCell>
                  <TableCell>
                    {member.last_login_at === null
                      ? 'Never'
                      : new Date(member.last_login_at).toLocaleString()}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  )
}
